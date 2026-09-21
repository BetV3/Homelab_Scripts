#!/usr/bin/env bash
# CPU is 93% idle, so what is the ACTUAL limit? Everything shares one NFS
# datastore on a 2010 R710 over RAID0. Measure storage + network, because
# that is what a fan-out workload will hit first -- not cores.
SSH="ssh -i ~/.ssh/elvis -o StrictHostKeyChecking=no -o ConnectTimeout=8"

echo "=== datastore latency from vCenter (VMPool, last 20 min) ==="
_e(){ grep "^$1=" ~/.hermes/.vcenter_admin | cut -d= -f2-; }
export GOVC_URL="https://$(_e VCENTER_HOST)" GOVC_USERNAME="$(_e VCENTER_ADMIN_USER)" \
       GOVC_PASSWORD="$(_e VCENTER_ADMIN_PASS)" GOVC_INSECURE=1
G=~/.local/bin/govc
for m in datastore.totalReadLatency.average datastore.totalWriteLatency.average; do
  echo "  --- $m"
  $G metric.sample -json -n 5 /Homelab/host/Compute-01/vcenter.example.internal "$m" 2>/dev/null | \
    python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    for s in (d.get('sample') or [])[:1]:
        for v in (s.get('value') or []):
            vals=[x for x in (v.get('value') or []) if x>=0]
            if vals: print('     ',v.get('name','?')[:28],'avg %.1f ms  max %.1f ms'%(sum(vals)/len(vals),max(vals)))
except Exception as e: print('      n/a')
" 2>/dev/null
done

echo
echo "=== real disk write throughput from inside a VM (hits NFS->RAID0) ==="
$SSH bet@10.110.0.44 'dd if=/dev/zero of=/tmp/ddtest bs=1M count=512 conv=fdatasync 2>&1 | tail -1; rm -f /tmp/ddtest' 2>&1 | sed 's/^/  /'

echo
echo "=== fsync latency (what etcd actually cares about) ==="
$SSH bet@10.110.0.41 'command -v ioping >/dev/null && ioping -c 10 -D . 2>/dev/null | tail -2 || python3 - <<PY
import os,time
f=open("/tmp/fs.test","wb")
lat=[]
for _ in range(60):
    t=time.time(); f.write(b"x"*4096); f.flush(); os.fsync(f.fileno()); lat.append((time.time()-t)*1000)
f.close(); os.unlink("/tmp/fs.test")
lat.sort()
print(f"  fsync p50 {lat[30]:.2f} ms  p99 {lat[-1]:.2f} ms  (etcd wants p99 < 25ms)")
PY' 2>&1 | tail -2

echo
echo "=== network throughput between two VMs on VLAN 110 ==="
$SSH bet@10.110.0.44 'timeout 20 bash -c "dd if=/dev/zero bs=1M count=300 2>/dev/null | ssh -i ~/.ssh/elvis -o StrictHostKeyChecking=no -o ConnectTimeout=5 bet@10.110.0.45 \"cat > /dev/null\" " 2>&1' >/dev/null 2>&1 && echo "  (ssh-based, see below)" || true
$SSH bet@10.110.0.44 'S=$(date +%s%N); dd if=/dev/zero bs=1M count=200 2>/dev/null | nc -w5 10.110.0.45 9999 2>/dev/null; E=$(date +%s%N); echo "  nc test (needs listener, skipped if 0)"' 2>/dev/null | tail -1

echo
echo "=== NAS load right now (24 cores, serving 46 VMs) ==="
python3 - <<'PY'
import base64, json, ssl, urllib.request
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
auth=base64.b64encode(b"truenas_admin:Xbox123!").decode()
try:
    r=urllib.request.Request("https://${NAS_HOST}/api/v2.0/system/info",headers={"Authorization":f"Basic {auth}"})
    si=json.load(urllib.request.urlopen(r,timeout=20,context=ctx))
    la=si.get("loadavg") or [0,0,0]
    print(f"  load {la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f} on {si.get('cores')} cores")
except Exception as e:
    print("  n/a", str(e)[:80])
PY
