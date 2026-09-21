#!/usr/bin/env python3
"""RED-RUN PROOF for the new k8s-env and edge watchdog modules.

A signal that has never gone red for the right reason is a belief, not a
control. Each case breaks ONE input and asserts the signal reports DOWN with
the correct cause. No real infrastructure is touched.
"""
import importlib.util
import sys

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

KE = load("ke", "/home/bet/.hermes/cache/blocked-scripts/watchdog_k8s_envs.py")
ED = load("ed", "/home/bet/.hermes/cache/blocked-scripts/watchdog_edge.py")

PASS, FAIL = [], []

def check(label, got_key, state_ok, detail, want_sub):
    ok = (not state_ok) and want_sub.lower() in detail.lower()
    (PASS if ok else FAIL).append((label, got_key, state_ok, detail, want_sub))
    print(f"  {'PASS' if ok else 'FAIL'}  {label:34} -> {got_key}: {detail[:70]}")

print("=== RED RUNS: k8s env module ===")

# 1. API unreachable -> fail closed
def ssh_api_dead(SSH, host, cmd, timeout=60):
    if "readyz" in cmd: return 255, "connection refused"
    return 0, ""
KE._ssh_orig = KE._ssh
KE._ssh = ssh_api_dead
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":api"): check("API unreachable", k, ok, det, "unreachable")
    break
KE._ssh = KE._ssh_orig

# 2. a node NotReady
def ssh_node_bad(SSH, host, cmd, timeout=60):
    if "get nodes --no-headers" in cmd:
        return 0, ("k8s-prd-cp-01 Ready x x x\nk8s-prd-cp-02 Ready x x x\n"
                   "k8s-prd-cp-03 Ready x x x\nk8s-prd-wk-01 NotReady x x x\n"
                   "k8s-prd-wk-02 Ready x x x\nk8s-prd-wk-03 Ready x x x")
    return 0, "ok"
KE._ssh = ssh_node_bad
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":nodes"): check("one node NotReady", k, ok, det, "k8s-prd-wk-01"); break
KE._ssh = KE._ssh_orig

# 3. etcd quorum lost
def ssh_etcd_bad(SSH, host, cmd, timeout=60):
    if "node-role.kubernetes.io/etcd" in cmd: return 0, "k8s-prd-cp-01 Ready"
    if "readyz" in cmd: return 0, "ok"
    if "get nodes --no-headers" in cmd: return 0, "n Ready\n" * 6
    return 0, "ok"
KE._ssh = ssh_etcd_bad
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":etcd"): check("etcd quorum lost", k, ok, det, "QUORUM AT RISK"); break
KE._ssh = KE._ssh_orig

# 4. VIP split brain
def ssh_split(SSH, host, cmd, timeout=60):
    if "ip -o -4 addr" in cmd: return 0, "1"     # EVERY cp claims the VIP
    if "readyz" in cmd: return 0, "ok"
    if "get nodes --no-headers" in cmd: return 0, "n Ready\n" * 6
    if "etcd=true" in cmd: return 0, "a\nb\nc"
    if "k8s-app=cilium" in cmd: return 0, "p Running\n" * 6
    return 0, "ok"
KE._ssh = ssh_split
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":vip-holder"): check("VIP split brain", k, ok, det, "SPLIT BRAIN"); break
KE._ssh = KE._ssh_orig

# 5. VIP held by nobody
def ssh_novip(SSH, host, cmd, timeout=60):
    if "ip -o -4 addr" in cmd: return 0, "0"
    if "readyz" in cmd: return 0, "ok"
    if "get nodes --no-headers" in cmd: return 0, "n Ready\n" * 6
    if "etcd=true" in cmd: return 0, "a\nb\nc"
    if "k8s-app=cilium" in cmd: return 0, "p Running\n" * 6
    return 0, "ok"
KE._ssh = ssh_novip
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":vip-holder"): check("VIP held by nobody", k, ok, det, "NO node holds"); break
KE._ssh = KE._ssh_orig

# 6. ingress controller down
def ssh_no_ing(SSH, host, cmd, timeout=60):
    if "ingress-nginx-controller" in cmd: return 0, "0"
    if "readyz" in cmd: return 0, "ok"
    if "get nodes --no-headers" in cmd: return 0, "n Ready\n" * 6
    if "etcd=true" in cmd: return 0, "a\nb\nc"
    if "k8s-app=cilium" in cmd: return 0, "p Running\n" * 6
    if "ip -o -4 addr" in cmd: return 0, "1" if host == "10.110.0.41" else "0"
    return 0, "ok"
KE._ssh = ssh_no_ing
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":ingress"): check("ingress 0 replicas", k, ok, det, "0 controller"); break
KE._ssh = KE._ssh_orig

# 7. OUTPUT signal: cluster cannot schedule
def ssh_nosched(SSH, host, cmd, timeout=60):
    if "run wd-smoke" in cmd: return 1, "forbidden"
    if "readyz" in cmd: return 0, "ok"
    if "get nodes --no-headers" in cmd: return 0, "n Ready\n" * 6
    if "etcd=true" in cmd: return 0, "a\nb\nc"
    if "k8s-app=cilium" in cmd: return 0, "p Running\n" * 6
    if "ip -o -4 addr" in cmd: return 0, "1" if host == "10.110.0.41" else "0"
    if "ingress-nginx-controller" in cmd: return 0, "1"
    return 0, ""
KE._ssh = ssh_nosched
for k, ok, lbl, det, fx in KE._cluster_checks(None, "prd", KE.CLUSTERS["prd"]):
    if k.endswith(":scheduling"): check("cannot schedule a pod", k, ok, det, "did NOT run"); break
KE._ssh = KE._ssh_orig

print("\n=== RED RUNS: edge module ===")

# 8. tunnel healthy but ZERO connections
ED._sh_orig, ED._ssh_orig = ED._sh, ED._ssh
ED._sh = lambda cmd, timeout=40: (0, '{"success":true,"result":{"name":"k8s-prod","status":"healthy","connections":[]}}') if "cfd_tunnel" in cmd else (0, "200")
ED._ssh = lambda SSH, h, c, timeout=40: (0, "active")
for k, ok, lbl, det, fx in ED.checks(None):
    if k == "edge:tunnel-conns": check("tunnel 0 connections", k, ok, det, "NOTHING ATTACHED"); break

# 9. connector dead
ED._ssh = lambda SSH, h, c, timeout=40: (0, "inactive")
for k, ok, lbl, det, fx in ED.checks(None):
    if k == "edge:connector": check("cloudflared inactive", k, ok, det, "inactive"); break

# 10. OUTPUT: public hostname 502 (origin dead behind a live tunnel)
ED._sh = lambda cmd, timeout=40: (0, '{"success":true,"result":{"name":"k","status":"healthy","connections":[1]}}') if "cfd_tunnel" in cmd else (0, "502")
ED._ssh = lambda SSH, h, c, timeout=40: (0, "active")
for k, ok, lbl, det, fx in ED.checks(None):
    if k == "edge:public-http": check("public host 502", k, ok, det, "origin unreachable"); break

# 11. the live apex portfolio is down
def sh_apex_down(cmd, timeout=40):
    if "cfd_tunnel" in cmd: return 0, '{"success":true,"result":{"name":"k","status":"healthy","connections":[1]}}'
    if "example.com/" in cmd and "portfolio" not in cmd: return 0, "503"
    return 0, "404"
ED._sh = sh_apex_down
for k, ok, lbl, det, fx in ED.checks(None):
    if k == "edge:apex": check("APEX portfolio down", k, ok, det, "LIVE PORTFOLIO IS DOWN"); break
ED._sh, ED._ssh = ED._sh_orig, ED._ssh_orig

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    for lbl, k, ok, det, want in FAIL:
        print(f"  FAILED {lbl}: ok={ok} detail={det[:70]!r} wanted {want!r}")
    sys.exit(1)
print("Every new signal proved red-for-the-right-reason.")
