#!/usr/bin/env python3
"""Full live infrastructure inventory. Every number read from a real system
at run time, so each one is re-runnable and defensible.

Writes JSON to the workspace so the map is built from data, not memory.
"""
import base64
import json
import os
import ssl
import subprocess
import urllib.request
from collections import defaultdict

OUT = "~/.hermes/cache/blocked-scripts/_inv"
os.makedirs(OUT, exist_ok=True)
inv = {}

# ---------- vCenter ----------
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


def gj(*a):
    p = subprocess.run([G, *a], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


hosts = gj("host.info", "-json", "*-vcenter.example.internal")
hl = []
for h in hosts.get("hostSystems", []) or hosts.get("HostSystems", []):
    s = h.get("summary", {})
    hw = s.get("hardware", {}) or {}
    qs = s.get("quickStats", {}) or {}
    name = (s.get("config", {}) or {}).get("name", "?").split("-mgmt")[0]
    if not hw.get("numCpuCores"):
        continue
    hl.append({
        "name": name,
        "model": hw.get("model", "?"),
        "cpu": hw.get("cpuModel", "?").strip(),
        "cores": hw.get("numCpuCores"),
        "ram_gb": round(hw.get("memorySize", 0) / 1073741824),
        "ram_used_gb": round(qs.get("overallMemoryUsage", 0) / 1024),
        "cpu_used_pct": round(100 * qs.get("overallCpuUsage", 0) /
                              (hw["numCpuCores"] * hw.get("cpuMhz", 1)), 1),
    })
inv["hosts"] = sorted(hl, key=lambda x: x["name"])

vms = gj("vm.info", "-json", "*")
vl = []
for v in vms.get("virtualMachines", []) or []:
    cfg = v.get("config", {}) or {}
    rt = v.get("runtime", {}) or {}
    gst = v.get("guest", {}) or {}
    hwx = cfg.get("hardware", {}) or {}
    vl.append({
        "name": cfg.get("name", "?"),
        "power": rt.get("powerState", "?"),
        "cpu": hwx.get("numCPU", 0),
        "ram_mb": hwx.get("memoryMB", 0),
        "ip": gst.get("ipAddress") or "",
        "guest": (gst.get("guestFullName") or "")[:40],
    })
inv["vms"] = sorted(vl, key=lambda x: x["name"])
inv["vm_on"] = sum(1 for v in vl if v["power"] == "poweredOn")

ds = gj("datastore.info", "-json", "*")
inv["datastores"] = []
for d in ds.get("datastores", []) or []:
    s = d.get("summary", {})
    if not s.get("capacity"):
        continue
    inv["datastores"].append({
        "name": s.get("name"),
        "cap_gb": round(s["capacity"] / 1e9),
        "free_gb": round(s.get("freeSpace", 0) / 1e9),
        "type": s.get("type", ""),
    })

# ---------- obs-01 metrics ----------
def vmq(q):
    try:
        r = subprocess.run(
            ["ssh", "-i", "~/.ssh/elvis", "-o", "StrictHostKeyChecking=no",
             "bet@${OBS_HOST}",
             f"curl -s --data-urlencode 'query={q}' http://localhost:8428/api/v1/query"],
            capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)["data"]["result"]
    except Exception:
        return []


inv["metrics"] = {}
r = vmq("count(up)")
inv["metrics"]["scrape_targets"] = int(float(r[0]["value"][1])) if r else 0
r = vmq("sum(up)")
inv["metrics"]["targets_up"] = int(float(r[0]["value"][1])) if r else 0
r = vmq('count(count by (instance) (node_cpu_seconds_total))')
inv["metrics"]["node_exporters"] = int(float(r[0]["value"][1])) if r else 0
fsync = {}
for x in vmq("histogram_quantile(0.99, sum by (le,env) "
             "(rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))"):
    fsync[x["metric"].get("env", "?")] = round(float(x["value"][1]) * 1000, 2)
inv["metrics"]["etcd_fsync_p99_ms"] = fsync
certs = {}
for x in vmq("(probe_ssl_earliest_cert_expiry - time())/86400"):
    certs[x["metric"].get("instance", "?")] = round(float(x["value"][1]), 1)
inv["metrics"]["cert_days"] = certs
r = vmq("sum(increase(vm_rows_inserted_total[1h]))")
inv["metrics"]["rows_per_hour"] = int(float(r[0]["value"][1])) if r else 0

# ---------- watchdog ----------
try:
    p = subprocess.run(
        ["ssh", "-i", "~/.ssh/elvis", "-o", "StrictHostKeyChecking=no",
         "bet@${WATCHDOG_HOST}",
         "python3 -c \"import json;d=json.load(open('~/.watchdog_alerts.json'));"
         "print(json.dumps({k:{'key':v.get('key'),'ok':v.get('ok')} for k,v in d.items()}))\""],
        capture_output=True, text=True, timeout=90)
    wd = json.loads(p.stdout)
    inv["watchdog"] = {
        "total": len(wd),
        "down": [v["key"] for v in wd.values() if v.get("ok") is False],
        "by_prefix": dict(sorted(
            ((k, v) for k, v in
             __import__("collections").Counter(
                 str(x["key"]).split(":")[0] for x in wd.values()).items()),
            key=lambda x: -x[1])),
    }
except Exception as e:
    inv["watchdog"] = {"error": str(e)[:80]}

# ---------- k8s ----------
inv["clusters"] = {}
for envn, ip in (("dev", "10.110.0.11"), ("stg", "10.110.0.31"), ("prd", "10.110.0.41")):
    try:
        p = subprocess.run(
            ["ssh", "-i", "~/.ssh/elvis", "-o", "StrictHostKeyChecking=no",
             f"bet@{ip}",
             "sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig "
             "/etc/rancher/rke2/rke2.yaml get nodes --no-headers | wc -l; "
             "sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig "
             "/etc/rancher/rke2/rke2.yaml get nodes --no-headers | grep -c ' Ready'; "
             "sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig "
             "/etc/rancher/rke2/rke2.yaml get pods -A --no-headers | wc -l"],
            capture_output=True, text=True, timeout=90)
        a = [x.strip() for x in p.stdout.split()]
        inv["clusters"][envn] = {"nodes": int(a[0]), "ready": int(a[1]), "pods": int(a[2])}
    except Exception as e:
        inv["clusters"][envn] = {"error": str(e)[:60]}

# ---------- DNS ----------
try:
    p = subprocess.run(
        ["ssh", "-i", "~/.ssh/elvis", "-o", "StrictHostKeyChecking=no",
         "bet@${DNS1}",
         "echo $(cat ~/.dnspw 2>/dev/null) | sudo -S pdnsutil list-zone "
         "home.example.com 2>/dev/null | grep -c 'IN\\sA\\s'"],
        capture_output=True, text=True, timeout=60)
    inv["dns_a_records"] = int(p.stdout.strip().split()[-1])
except Exception:
    inv["dns_a_records"] = None

# ---------- crons ----------
try:
    p = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    inv["system_crons"] = len([l for l in p.stdout.splitlines()
                               if l.strip() and not l.startswith("#")])
except Exception:
    inv["system_crons"] = None

with open(f"{OUT}/inventory.json", "w") as f:
    json.dump(inv, f, indent=1)

# ---------- summary ----------
print("=" * 64)
print("HOSTS")
tc = sum(h["cores"] for h in inv["hosts"])
tr = sum(h["ram_gb"] for h in inv["hosts"])
tu = sum(h["ram_used_gb"] for h in inv["hosts"])
for h in inv["hosts"]:
    print(f"  {h['name']:8} {h['model'][:22]:22} {h['cores']:3}c "
          f"{h['ram_used_gb']:4}/{h['ram_gb']:3}GB  cpu {h['cpu_used_pct']:4}%")
print(f"  TOTAL: {len(inv['hosts'])} hosts, {tc} cores, {tu}/{tr} GB used")
print()
print(f"VMs: {inv['vm_on']} powered on of {len(inv['vms'])}")
print(f"Clusters: {inv['clusters']}")
print(f"Scrape targets: {inv['metrics']['targets_up']}/{inv['metrics']['scrape_targets']}"
      f"  node_exporters: {inv['metrics']['node_exporters']}")
print(f"etcd fsync p99 ms: {inv['metrics']['etcd_fsync_p99_ms']}")
print(f"TSDB rows/hour: {inv['metrics']['rows_per_hour']:,}")
print(f"Watchdog: {inv['watchdog'].get('total')} signals, "
      f"down={inv['watchdog'].get('down')}")
print(f"  by prefix: {inv['watchdog'].get('by_prefix')}")
print(f"DNS A records: {inv['dns_a_records']}  system crons: {inv['system_crons']}")
print()
print("DATASTORES")
for d in inv["datastores"]:
    print(f"  {d['name']:16} {d['cap_gb']-d['free_gb']:6}/{d['cap_gb']:6} GB used  {d['type']}")
print()
print(f"written: {OUT}/inventory.json")
