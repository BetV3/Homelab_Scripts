#!/usr/bin/env bash
# Fix: prd cp-02 and cp-03 were joined without tls-san, so whichever of them
# holds the VIP serves an apiserver cert that is NOT valid for 10.110.0.40.
# Adds the same tls-san block cp-01 has, restarts rke2-server one node at a
# time (3-member etcd, quorum holds), moves the VIP off a node before
# restarting it, and measures the API blip through the VIP the whole time.
# Rollback: restore config.yaml.bak-<date> and restart rke2-server.
set -uo pipefail
SSH="ssh -i ~/.ssh/admin -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"
K="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
VIP=10.110.0.40; CP1=10.110.0.41
KC=~/.kube/config-prd
LOG=/tmp/prd_san_fix.$(date +%s).log
STAMP=$(date +%Y%m%d-%H%M)

san_block='tls-san:
  - 10.110.0.40
  - 10.110.0.41
  - 10.110.0.42
  - 10.110.0.43
  - 10.110.0.44
  - 10.110.0.45
  - 10.110.0.46
  - api.prd.k8s.example.internal'

# background probe: real client path, cert verified against the cluster CA
probe() {
  while true; do
    code=$(timeout 3 kubectl --kubeconfig $KC get --raw /readyz 2>/dev/null) || code="FAIL"
    echo "$(date +%s.%N | cut -c1-14) $code"
    sleep 0.5
  done
}
probe > $LOG & PROBE=$!

holder() { for ip in 10.110.0.41 10.110.0.42 10.110.0.43; do $SSH bet@$ip "ip -4 -br addr show | grep -q $VIP && echo $ip"; done; }
cert_has_vip() { echo | timeout 8 openssl s_client -connect $1:6443 2>/dev/null | openssl x509 -noout -ext subjectAltName | grep -c "IP Address:$VIP"; }
etcd_members() { $SSH bet@$CP1 "$K -n kube-system get pod -l component=etcd --no-headers | grep -c Running"; }

fix_node() {
  ip=$1
  echo "--- $ip: before cert_has_vip=$(cert_has_vip $ip) etcd_running=$(etcd_members)"
  $SSH bet@$ip "sudo cp /etc/rancher/rke2/config.yaml /etc/rancher/rke2/config.yaml.bak-$STAMP && printf '%s\n' '$san_block' | sudo tee -a /etc/rancher/rke2/config.yaml >/dev/null && sudo grep -c tls-san /etc/rancher/rke2/config.yaml"
  t0=$(date +%s)
  $SSH bet@$ip "sudo systemctl restart rke2-server"
  for i in $(seq 1 60); do
    sleep 2
    ok=$($SSH bet@$ip "sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get --raw /readyz 2>/dev/null")
    [ "$ok" = "ok" ] && break
  done
  echo "--- $ip: rke2-server ready after $(( $(date +%s) - t0 ))s; cert_has_vip=$(cert_has_vip $ip) etcd_running=$(etcd_members)"
}

echo "=== START $(date -u +%FT%TZ)  holder=$(holder | tr '\n' ' ') vip_cert_has_vip=$(cert_has_vip $VIP)"

# 1) cp-02 (not the holder)
fix_node 10.110.0.42

# 2) move the VIP off cp-03 (delete the kube-vip pod on the holder) and wait for a new holder
H=$(holder | head -1); echo "=== VIP holder now $H"
if [ "$H" = "10.110.0.43" ]; then
  POD=$($SSH bet@$CP1 "$K -n kube-system get pod -l name=kube-vip-ds -o wide --no-headers | awk '/k8s-prd-cp-03/{print \$1}'")
  echo "=== deleting kube-vip pod $POD on cp-03 at $(date +%s.%N | cut -c1-14)"
  $SSH bet@$CP1 "$K -n kube-system delete pod $POD --wait=false"
  for i in $(seq 1 30); do sleep 1; NH=$(holder | tr '\n' ' '); case "$NH" in *10.110.0.43*|"") ;; *) break;; esac; done
  echo "=== VIP moved to [$NH] at $(date +%s.%N | cut -c1-14) after ${i}s; vip_cert_has_vip=$(cert_has_vip $VIP)"
fi

# 3) cp-03
fix_node 10.110.0.43

kill $PROBE 2>/dev/null; wait $PROBE 2>/dev/null
echo "=== END $(date -u +%FT%TZ)  holder=$(holder | tr '\n' ' ') vip_cert_has_vip=$(cert_has_vip $VIP) nodes_ready=$(kubectl --kubeconfig $KC get nodes --no-headers 2>/dev/null | grep -c ' Ready')"
echo "=== probe: $(wc -l < $LOG) samples, $(grep -c ' ok$' $LOG) ok, $(grep -c FAIL $LOG) failed  (log $LOG)"
echo "=== probe fail windows:"; awk '$2=="FAIL"{if(!s)s=$1;e=$1;n++} $2=="ok"&&s{printf "   %.1fs (%d samples) from %s\n", e-s+0.5, n, s; s="";n=0}' $LOG
