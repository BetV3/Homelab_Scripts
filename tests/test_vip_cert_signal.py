#!/usr/bin/env python3
"""Red/green for k8s:<env>:vip-cert and the fixed k8s:<env>:api.

RED: replace _ssh with a fake that answers the SAN query without the VIP for
one CP (the exact pre-fix state of prd cp-02/cp-03) and makes /readyz via the
VIP fail with the x509 error kubectl printed on 2026-10-08. Both signals must
report DOWN. GREEN: run the real module against the live clusters.
Exit non-zero if any expectation fails, so this cannot pass by accident.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "monitoring"))
import watchdog_k8s_envs as m

GOOD_SAN = ("X509v3 Subject Alternative Name: \n"
            "    DNS:kubernetes, IP Address:10.110.0.40, IP Address:10.110.0.41")
BAD_SAN = ("X509v3 Subject Alternative Name: \n"
           "    DNS:kubernetes, IP Address:10.110.0.43, IP Address:10.43.0.1")
X509 = ('Get "https://10.110.0.40:6443/readyz": tls: failed to verify certificate: '
        'x509: certificate is valid for 127.0.0.1, ::1, 10.110.0.43, 10.43.0.1, not 10.110.0.40')

def fake_ssh_factory(bad_cps, api_fails):
    def fake(SSH, host, cmd, timeout=60):
        if "subjectAltName" in cmd:
            return 0, BAD_SAN if host in bad_cps else GOOD_SAN
        if "/readyz" in cmd and "--server" in cmd:
            return (1, X509) if api_fails else (0, "ok")
        return 1, "not-exercised"      # every other check fails closed; ignored below
    return fake

def run(env, fake):
    m._ssh = fake
    out = {}
    for key, ok, label, detail, fix in m._cluster_checks([], env, m.CLUSTERS[env]):
        out[key] = (ok, detail)
    return out

failures = 0
def expect(name, cond, detail):
    global failures
    print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}")
    if not cond: failures += 1

real_ssh = m._ssh

# RED 1: pre-fix prd state. cp-02 and cp-03 lack the SAN, VIP on cp-03 -> API via VIP fails.
r = run("prd", fake_ssh_factory({"10.110.0.42", "10.110.0.43"}, api_fails=True))
expect("RED vip-cert names both bad CPs", r["k8s:prd:vip-cert"][0] is False
       and "10.110.0.42,10.110.0.43" in r["k8s:prd:vip-cert"][1], r["k8s:prd:vip-cert"][1])
expect("RED api via VIP is DOWN on x509", r["k8s:prd:api"][0] is False
       and "x509" in r["k8s:prd:api"][1], r["k8s:prd:api"][1][:90])

# RED 2: latent state. Holder is fine (API ok) but one standby lacks the SAN.
r = run("prd", fake_ssh_factory({"10.110.0.42"}, api_fails=False))
expect("RED latent: api green, vip-cert DOWN on standby", r["k8s:prd:api"][0] is True
       and r["k8s:prd:vip-cert"][0] is False and "10.110.0.42" in r["k8s:prd:vip-cert"][1],
       r["k8s:prd:vip-cert"][1])

# CONTROL: all good -> both green
r = run("prd", fake_ssh_factory(set(), api_fails=False))
expect("CONTROL both green", r["k8s:prd:api"][0] and r["k8s:prd:vip-cert"][0], r["k8s:prd:vip-cert"][1])

# GREEN: live clusters, real ssh, only the two signals under test
m._ssh = real_ssh
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/admin"), "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8"]
for env in ("stg", "prd"):
    for key, ok, label, detail, fix in m._cluster_checks(SSH, env, m.CLUSTERS[env]):
        if key.endswith(":api") or key.endswith(":vip-cert"):
            expect(f"GREEN live {key}", ok, detail)
        if key.endswith(":vip-cert"):
            break   # stop before the smoke pod; the full module runs on the watchdog host

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
