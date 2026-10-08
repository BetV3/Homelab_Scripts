#!/usr/bin/env python3
"""Red/green for k8s:<env>:gitops and k8s:<env>:admission.

RED cases fake the kubectl JSON for: a Kustomization Ready=False, a suspended one,
two Kustomizations on different revisions (silent drift), admission 0 replicas,
policy not Ready. Then GREEN against the live clusters. Exits 1 on any miss.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "monitoring"))
import watchdog_k8s_envs as m

def ks(name, ready=True, rev="main@sha1:abc12345", suspend=False, msg="Applied"):
    return {"metadata": {"name": name},
            "spec": {"suspend": suspend},
            "status": {"lastAppliedRevision": rev,
                       "conditions": [{"type": "Ready", "status": "True" if ready else "False", "message": msg}]}}

def fake_factory(items, adm="1", pol="True"):
    def fake(SSH, host, cmd, timeout=60):
        if "get kustomization -o json" in cmd:
            return 0, json.dumps({"items": items})
        if "kyverno-admission-controller" in cmd:
            return 0, f"'{adm}'\n'{pol}'"
        return 1, "not-exercised"
    return fake

def run(fake):
    m._ssh = fake
    return {k: (ok, d) for k, ok, _l, d, _f in m._cluster_checks([], "prd", m.CLUSTERS["prd"])}

fails = 0
def expect(name, cond, detail):
    global fails
    print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}")
    if not cond: fails += 1

real = m._ssh
good = [ks("flux-system"), ks("infra"), ks("policies"), ks("apps")]

r = run(fake_factory(good))
expect("CONTROL gitops green", r["k8s:prd:gitops"][0], r["k8s:prd:gitops"][1])
expect("CONTROL admission green", r["k8s:prd:admission"][0], r["k8s:prd:admission"][1])

r = run(fake_factory([ks("flux-system"), ks("infra"), ks("apps", ready=False, msg="health check failed after 3m0s")]))
expect("RED Kustomization not Ready", not r["k8s:prd:gitops"][0] and "apps" in r["k8s:prd:gitops"][1], r["k8s:prd:gitops"][1])

r = run(fake_factory([ks("flux-system"), ks("infra", suspend=True), ks("apps")]))
expect("RED suspended Kustomization", not r["k8s:prd:gitops"][0] and "suspended" in r["k8s:prd:gitops"][1], r["k8s:prd:gitops"][1])

r = run(fake_factory([ks("flux-system", rev="main@sha1:abc12345"), ks("infra", rev="main@sha1:abc12345"), ks("apps", rev="main@sha1:deadbeef")]))
expect("RED revisions diverge (silent drift, all Ready)", not r["k8s:prd:gitops"][0] and "diverge" in r["k8s:prd:gitops"][1], r["k8s:prd:gitops"][1])

r = run(fake_factory([]))
expect("RED no Kustomizations at all", not r["k8s:prd:gitops"][0], r["k8s:prd:gitops"][1])

r = run(fake_factory(good, adm="0"))
expect("RED admission 0 ready", not r["k8s:prd:admission"][0], r["k8s:prd:admission"][1])

r = run(fake_factory(good, pol="False"))
expect("RED policy not Ready", not r["k8s:prd:admission"][0], r["k8s:prd:admission"][1])

r = run(fake_factory(good, pol=""))
expect("RED policy missing", not r["k8s:prd:admission"][0], r["k8s:prd:admission"][1])

m._ssh = real
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/admin"), "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8"]
for env in ("stg", "prd"):
    seen = 0
    for key, ok, _l, detail, _f in m._cluster_checks(SSH, env, m.CLUSTERS[env]):
        if key.endswith((":gitops", ":admission")):
            expect(f"GREEN live {key}", ok, detail); seen += 1
        if seen == 2: break

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
