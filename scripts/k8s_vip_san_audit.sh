#!/usr/bin/env bash
# Read-only audit: which control planes carry the VIP in their apiserver cert SAN,
# who holds the VIP now, and since when. Covers dev (.10) and prd (.40).
set -uo pipefail
SSH="ssh -i ~/.ssh/admin -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"
K="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

for grp in "dev:10.110.0.10:10.110.0.11,10.110.0.12,10.110.0.13" \
           "prd:10.110.0.40:10.110.0.41,10.110.0.42,10.110.0.43"; do
  env=${grp%%:*}; rest=${grp#*:}; vip=${rest%%:*}; cps=${rest#*:}
  echo "=== $env VIP $vip"
  for ip in ${cps//,/ }; do
    san=$(echo | timeout 8 openssl s_client -connect $ip:6443 2>/dev/null | openssl x509 -noout -ext subjectAltName 2>/dev/null | tail -1)
    has=$(echo "$san" | grep -c "IP Address:$vip")
    holder=$(timeout 15 $SSH bet@$ip "ip -4 -br addr show | grep -c $vip" 2>/dev/null)
    tlssan=$(timeout 15 $SSH bet@$ip "sudo grep -c 'tls-san' /etc/rancher/rke2/config.yaml" 2>/dev/null)
    echo "  $ip  cert_has_vip=$has  holds_vip=$holder  config_tls_san=$tlssan"
  done
  first=${cps%%,*}
  echo "  lease:"; timeout 20 $SSH bet@$first "$K -n kube-system get lease plndr-cp-lock -o jsonpath='{.spec.holderIdentity} acquired={.spec.acquireTime} renewed={.spec.renewTime}{\"\\n\"}'" 2>&1
  echo -n "  VIP serves cert with VIP SAN: "; echo | timeout 8 openssl s_client -connect $vip:6443 2>/dev/null | openssl x509 -noout -ext subjectAltName 2>/dev/null | grep -c "IP Address:$vip"
done
