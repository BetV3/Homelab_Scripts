#!/usr/bin/env python3
"""Current infrastructure metrics: CPU, memory, storage. Live from vCenter + TrueNAS.

REAL usage (quickStats), not just allocation. Allocation is what the capacity
planner tracks; usage is what is actually being consumed. They differ a lot
here -- that gap is the whole reason the dev workers were right-sized.
"""
import json
import os
import ssl
import subprocess
import urllib.request

_env = {}
for _l in open("~/.hermes/.vcenter_admin"):
    _l = _l.strip()
    if "=" in _l and not _l.startswith("#"):
        k, v = _l.split("=", 1)
        _env[k] = v
os.environ.update(
    GOVC_URL=f"https://{_env['VCENTER_HOST']}",
    GOVC_USERNAME=_env["VCENTER_ADMIN_USER"],
    GOVC_PASSWORD=_env["VCENTER_ADMIN_PASS"],
    GOVC_INSECURE="1",
)
G = "~/.local/bin/govc"


def govc_json(*args):
    p = subprocess.run([G, *args], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


# ---------- hosts: CPU + memory, allocated vs actually used ----------
hosts = govc_json("host.info", "-json", "*-vcenter.example.internal")
rows = []
tc = tm = uc = um = 0
tcores = 0
for h in hosts.get("hostSystems", []) or hosts.get("HostSystems", []):
    s = h.get("summary", {})
    hw = s.get("hardware", {}) or {}
    qs = s.get("quickStats", {}) or {}
    name = (s.get("config", {}) or {}).get("name", "?").split("-mgmt")[0]
    cores = hw.get("numCpuCores", 0)
    mhz = hw.get("cpuMhz", 0)
    cap_cpu = cores * mhz
    mem_gb = hw.get("memorySize", 0) / 1073741824
    use_cpu = qs.get("overallCpuUsage", 0)
    use_mem = qs.get("overallMemoryUsage", 0) / 1024
    if not cap_cpu:
        continue
    rows.append((name, cores, cap_cpu, use_cpu, mem_gb, use_mem))
    tc += cap_cpu; uc += use_cpu; tm += mem_gb; um += use_mem; tcores += cores

print("=" * 66)
print("COMPUTE — real usage (vCenter quickStats)")
print("=" * 66)
print(f"{'host':8} {'cores':>5} {'CPU used':>16} {'RAM used':>18}")
for n, cores, cc, ucpu, mg, umem in sorted(rows):
    cp = 100 * ucpu / cc if cc else 0
    mp = 100 * umem / mg if mg else 0
    print(f"{n:8} {cores:5} {ucpu/1000:6.1f}/{cc/1000:5.1f} GHz {cp:4.1f}%"
          f" {umem:6.1f}/{mg:5.1f} GB {mp:5.1f}%")
print("-" * 66)
print(f"{'TOTAL':8} {tcores:5} {uc/1000:6.1f}/{tc/1000:5.1f} GHz "
      f"{100*uc/tc:4.1f}% {um:6.1f}/{tm:5.1f} GB {100*um/tm:5.1f}%")

# ---------- datastores ----------
print()
print("=" * 66)
print("STORAGE")
print("=" * 66)
ds = govc_json("datastore.info", "-json", "*")
for d in ds.get("datastores", []) or ds.get("Datastores", []):
    s = d.get("summary", {})
    cap = s.get("capacity", 0) / 1e9
    free = s.get("freeSpace", 0) / 1e9
    if not cap:
        continue
    used = cap - free
    print(f"  {s.get('name','?'):14} {used:7.0f}/{cap:7.0f} GB used "
          f"({100*used/cap:4.1f}%)   free {free:7.0f} GB")

# ---------- powered-on VM allocation, for the alloc-vs-use gap ----------
vms = govc_json("ls", "-json", "-l", "/Homelab/vm/...")
print()
p = subprocess.run([G, "vm.info", "-json", "*"], capture_output=True, text=True)
try:
    vd = json.loads(p.stdout)
    on = [v for v in (vd.get("virtualMachines") or [])
          if (v.get("runtime", {}) or {}).get("powerState") == "poweredOn"]
    alloc = sum((v.get("config", {}) or {}).get("hardware", {}).get("memoryMB", 0)
                for v in on) / 1024
    print(f"  powered-on VMs: {len(on)}   RAM ALLOCATED {alloc:.0f} GB "
          f"vs {um:.0f} GB actually used  ({100*um/alloc if alloc else 0:.0f}%)")
except Exception as e:
    print("  vm alloc: unavailable", e)

# ---------- TrueNAS pool ----------
print()
print("=" * 66)
print("TrueNAS pool 'lab' (4-wide RAID0, backs every VM)")
print("=" * 66)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
import base64
auth = base64.b64encode(b"truenas_admin:Xbox123!").decode()
try:
    rq = urllib.request.Request("https://${NAS_HOST}/api/v2.0/pool",
                                headers={"Authorization": f"Basic {auth}"})
    pools = json.load(urllib.request.urlopen(rq, timeout=25, context=ctx))
    for pl in pools:
        size = pl.get("size") or 0
        alloc = pl.get("allocated") or 0
        free = pl.get("free") or 0
        print(f"  {pl.get('name'):8} {pl.get('status'):8} "
              f"{alloc/1e12:5.2f}/{size/1e12:5.2f} TB used "
              f"({100*alloc/size if size else 0:4.1f}%)  free {free/1e12:5.2f} TB")
    rq = urllib.request.Request("https://${NAS_HOST}/api/v2.0/system/info",
                                headers={"Authorization": f"Basic {auth}"})
    si = json.load(urllib.request.urlopen(rq, timeout=25, context=ctx))
    la = si.get("loadavg") or [0, 0, 0]
    print(f"  NAS: {si.get('physmem',0)/1e9:.0f} GB RAM, {si.get('cores')} cores, "
          f"load {la[0]:.2f}, up {si.get('uptime','?')}")
except Exception as e:
    print("  TrueNAS query failed:", str(e)[:120])
