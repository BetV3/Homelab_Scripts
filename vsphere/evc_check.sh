#!/usr/bin/env bash
# Can the AVX2 mask be lifted? EVC baseline must be <= the OLDEST host's CPU.
# If every host is Haswell, raising the baseline to Haswell restores AVX2
# fleet-wide -- a free ~2x on every numeric workload.
_e(){ grep "^$1=" ~/.hermes/.vcenter_admin | cut -d= -f2-; }
export GOVC_URL="https://$(_e VCENTER_HOST)" GOVC_USERNAME="$(_e VCENTER_ADMIN_USER)" \
       GOVC_PASSWORD="$(_e VCENTER_ADMIN_PASS)" GOVC_INSECURE=1
G=~/.local/bin/govc

echo "=== EVC baseline ==="
$G object.collect -s /Homelab/host/Compute-01 summary.currentEVCModeKey 2>/dev/null | sed 's/^/  current: /'
$G object.collect -s /Homelab/host/Compute-01 summary.maxEVCModeKey 2>/dev/null | sed 's/^/  max:     /'

echo
echo "=== CPU model per host (the oldest one sets the ceiling) ==="
for h in esxi02 esxi03 esxi05 esxi06 esxi07 esxi08 esxi09; do
  $G host.info -json "$vcenter.example.internal" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
hs=(d.get('hostSystems') or d.get('HostSystems') or [{}])[0]
hw=(hs.get('summary',{}).get('hardware',{}) or {})
print('  $h:', hw.get('cpuModel','?').strip())
" 2>/dev/null
done

echo
echo "=== memory bandwidth actually available in a guest ==="
echo "  (decode speed for CPU inference is bandwidth/model_size)"
ssh -i ~/.ssh/elvis -o StrictHostKeyChecking=no bet@10.110.0.44 \
  'command -v mbw >/dev/null 2>&1 && mbw -q -n 3 512 2>/dev/null | tail -1 || \
   python3 - <<PY
import time
n=200*1024*1024
a=bytearray(n); b=bytearray(n)
t=time.time()
for _ in range(3): b[:]=a
d=time.time()-t
print(f"  memcpy ~{3*n/d/1e9:.1f} GB/s (read+write, so ~{2*3*n/d/1e9:.1f} GB/s traffic)")
PY' 2>&1 | tail -3
