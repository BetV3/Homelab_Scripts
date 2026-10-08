#!/usr/bin/env python3
"""Watchdog checks for the staging and prod RKE2 clusters.

Imported by ~/watchdog.py on langfuse-01 (system cron */5, OUTSIDE Hermes).
Contract: checks(SSH) -> yields (key, ok, label, detail, fix).

This is watchdog_k8s.py generalised over three clusters. The dev/mgmt cluster
keeps its own module (stable IDs already cited in the vault); this one adds
`stg` and `prd` with the same check shapes and the same rules:

  * OUTPUT signals, not just health signals. "All nodes Ready" is the k8s
    equivalent of "cron ran". k8s:<env>:scheduling watches the outcome.
  * Fail CLOSED. A check that cannot run is DOWN, not silence.
  * Test through the VIP, not a node IP. A node-IP check passes while the VIP
    every client actually uses is dead.
  * Smoke-pod names must be unique ACROSS CONCURRENT RUNS -- seconds
    resolution is not (that produced a false outage on the mgmt cluster).

NOTE: langfuse-01 reaches these nodes with ~/.ssh/watchdog. cloud-init only
installed elvis.pub, so that key had to be added to all 9 nodes first --
otherwise every check here fails closed on a perfectly healthy cluster.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid

ASSISTANT = "${ASSISTANT_HOST}"

KCTL = ("sudo /var/lib/rancher/rke2/bin/kubectl "
        "--kubeconfig /etc/rancher/rke2/rke2.yaml")

CLUSTERS = {
    "stg": {
        "cp1": "10.110.0.31",
        "vip": "10.110.0.30",
        "cps": ["10.110.0.31"],
        "nodes": 3,
        "etcd": 1,          # single control plane by design (pre-prod)
        "ingress_np": 30080,
    },
    "prd": {
        "cp1": "10.110.0.41",
        "vip": "10.110.0.40",
        "cps": ["10.110.0.41", "10.110.0.42", "10.110.0.43"],
        "nodes": 6,
        "etcd": 3,
        "ingress_np": 30080,
    },
}


def _ssh(SSH, host, cmd, timeout=60):
    try:
        p = subprocess.run(SSH + [f"bet@{host}", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _cluster_checks(SSH, env, c):
    cp1, vip = c["cp1"], c["vip"]
    fix = f"ssh bet@{cp1} '{KCTL} get nodes'"
    P = f"k8s:{env}"

    # ---- API through the VIP -------------------------------------------
    # --server forces the request through the VIP with the cluster CA from
    # rke2.yaml still verifying the cert. Without --server this check used
    # 127.0.0.1 and said "via VIP" while never touching it: on 2026-10-08 the
    # prd VIP served a cert without 10.110.0.40 in its SANs for 9 days and this
    # signal stayed green the whole time.
    rc, out = _ssh(SSH, cp1, f"{KCTL} --server https://{vip}:6443 get --raw /readyz", 40)
    yield (f"{P}:api", rc == 0 and "ok" in out.lower(),
           f"{env} API (via VIP)",
           f"API via {vip} ok" if rc == 0 else f"unreachable: {out[:110]}", fix)

    # ---- every CP's served cert must be valid for the VIP ---------------
    # Latent-risk signal: the holder can be fine while a standby CP would
    # serve a bad cert the moment failover hands it the VIP. RKE2 only adds
    # tls-san entries from the node's OWN config.yaml, so a CP joined without
    # the block is silently invalid for the VIP until it becomes the holder.
    missing = []
    for ip in c["cps"]:
        rc, out = _ssh(SSH, ip,
                       "echo | openssl s_client -connect 127.0.0.1:6443 2>/dev/null"
                       " | openssl x509 -noout -ext subjectAltName", 20)
        if rc != 0 or f"IP Address:{vip}" not in out:
            missing.append(ip)
    yield (f"{P}:vip-cert", not missing, f"{env} apiserver certs valid for VIP",
           f"{len(c['cps'])}/{len(c['cps'])} control planes carry {vip} in SAN"
           if not missing else
           f"cert NOT valid for {vip} on: {','.join(missing)}"
           " -- failover to that node breaks every kubeconfig",
           "add the tls-san block to /etc/rancher/rke2/config.yaml on that node "
           "and restart rke2-server (bash scripts/prd_san_fix.sh)")

    # ---- nodes ----------------------------------------------------------
    rc, out = _ssh(SSH, cp1, f"{KCTL} get nodes --no-headers", 40)
    if rc != 0:
        yield (f"{P}:nodes", False, f"{env} nodes Ready",
               f"cannot list nodes: {out[:110]}", fix)
    else:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        ready = [ln for ln in lines if " Ready " in f" {ln} "]
        bad = [ln.split()[0] for ln in lines if " Ready " not in f" {ln} "]
        yield (f"{P}:nodes", len(ready) == c["nodes"] and not bad,
               f"{env} nodes Ready",
               f"{len(ready)}/{c['nodes']} Ready"
               + (f", not-ready: {','.join(bad)}" if bad else ""), fix)

    # ---- etcd quorum ----------------------------------------------------
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} get nodes -l node-role.kubernetes.io/etcd=true "
                   "--no-headers", 40)
    n = len([ln for ln in out.splitlines() if ln.strip()]) if rc == 0 else 0
    yield (f"{P}:etcd", rc == 0 and n >= c["etcd"], f"{env} etcd members",
           f"{n}/{c['etcd']} etcd members"
           + ("" if n >= c["etcd"] else " -- QUORUM AT RISK"), fix)

    # ---- cilium ---------------------------------------------------------
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} get pods -n kube-system -l k8s-app=cilium "
                   "--no-headers", 40)
    if rc != 0:
        yield (f"{P}:cilium", False, f"{env} CNI (cilium)",
               f"cannot list cilium pods: {out[:110]}", fix)
    else:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        run = [ln for ln in lines if " Running " in f" {ln} "]
        yield (f"{P}:cilium", bool(lines) and len(run) == len(lines),
               f"{env} CNI (cilium)",
               f"{len(run)}/{len(lines)} cilium pods Running" if lines
               else "no cilium pods found -- CNI MISSING", fix)

    # ---- VIP holder: 0 = outage, 2+ = split brain ------------------------
    holders = []
    for ip in c["cps"]:
        rc, out = _ssh(SSH, ip, f"ip -o -4 addr show | grep -c ' {vip}/' || true", 20)
        if rc == 0 and out.strip() not in ("", "0"):
            holders.append(ip)
    if len(holders) == 1:
        ok, detail = True, f"VIP held by {holders[0]}"
    elif not holders:
        ok, detail = False, f"NO node holds {vip}"
    else:
        ok, detail = False, f"SPLIT BRAIN: {holders} all hold {vip}"
    yield (f"{P}:vip-holder", ok, f"{env} VIP ownership", detail,
           f"ssh bet@{cp1} '{KCTL} -n kube-system get pods "
           "-l app.kubernetes.io/name=kube-vip-ds'")

    # ---- ingress controller ---------------------------------------------
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} -n ingress-nginx get deploy ingress-nginx-controller "
                   "-o jsonpath='{.status.readyReplicas}'", 40)
    rr = (out or "0").strip().strip("'")
    ready_n = int(rr) if rr.isdigit() else 0
    yield (f"{P}:ingress", rc == 0 and ready_n >= 1, f"{env} ingress-nginx",
           f"{ready_n} controller replica(s) Ready" if rc == 0
           else f"cannot query: {out[:100]}",
           f"ssh bet@{cp1} '{KCTL} -n ingress-nginx get pods'")

    # ---- OUTPUT SIGNAL: can it actually run a workload? -----------------
    name = f"wd-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} run {name} --labels wd=smoke --image=busybox:1.36 "
                   "--restart=Never -- echo ok", 40)
    phase = ""
    if rc == 0:
        for _ in range(12):
            time.sleep(4)
            _, phase = _ssh(SSH, cp1,
                            f"{KCTL} get pod {name} -o jsonpath='{{.status.phase}}'", 25)
            phase = phase.strip().strip("'")
            if phase in ("Succeeded", "Failed"):
                break
    _ssh(SSH, cp1, f"{KCTL} delete pod {name} --ignore-not-found --wait=false", 30)
    # Sweep ONLY pods old enough to be from a dead run, never a live one.
    _ssh(SSH, cp1,
         f"{KCTL} get pods -l wd=smoke --no-headers 2>/dev/null | "
         "awk '$5 ~ /[0-9]+[hd]/ {print $1}' | "
         f"xargs -r {KCTL} delete pod --ignore-not-found --wait=false", 40)
    yield (f"{P}:scheduling", phase == "Succeeded", f"{env} can schedule a pod",
           "test pod scheduled and completed" if phase == "Succeeded"
           else f"test pod did NOT run (phase={phase or 'none'})",
           f"ssh bet@{cp1} '{KCTL} get events -A --sort-by=.lastTimestamp | tail -20'")

    # ---- backup freshness (restic lives on assistant) -------------------
    sftp = ("ssh -i ~/.ssh/admin -o BatchMode=yes "
            "-o StrictHostKeyChecking=no bet@${WATCHDOG_HOST} -s sftp")
    cmd = ("RESTIC_PASSWORD_FILE=~/.restic_pw "
           "RESTIC_REPOSITORY=sftp:bet@${WATCHDOG_HOST}:~/restic-repo "
           "RESTIC_PROGRESS_FPS=0 "
           f"restic -o 'sftp.command={sftp}' snapshots --tag k8s-{env} "
           "--json --latest 1 2>/dev/null")
    rc, out = _ssh(SSH, ASSISTANT, cmd, 150)
    ok, detail = False, "backup check failed"
    if rc == 0 and out.strip().startswith("["):
        try:
            snaps = json.loads(out)
            if snaps:
                tstr = snaps[-1]["time"][:19]
                age_h = (time.time() - time.mktime(
                    time.strptime(tstr, "%Y-%m-%dT%H:%M:%S"))) / 3600
                ok = age_h <= 36
                detail = f"newest etcd backup {age_h:.0f}h old"
            else:
                detail = f"no k8s-{env} snapshot in restic"
        except Exception as e:  # noqa: BLE001
            detail = f"parse failed: {str(e)[:80]}"
    else:
        detail = f"restic failed: {out[:100]}"
    yield (f"{P}:backup", ok, f"{env} etcd backup freshness", detail,
           "bash scripts/k8s_backup_envs.sh")

    # ---- GitOps: is Flux reconciled, and is it reconciling the CURRENT commit? ----
    # Two different failures. Ready=False is "the apply or health check failed".
    # Ready=True on a stale revision is "git moved and the cluster did not", which
    # is the silent one: a dead deploy key or a suspended source looks healthy forever.
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} -n flux-system get kustomization -o json", 40)
    if rc != 0 or not out.strip().startswith("{"):
        yield (f"{P}:gitops", False, f"{env} Flux Kustomizations",
               f"cannot read flux-system: {out[:100]}",
               f"ssh bet@{cp1} '{KCTL} -n flux-system get kustomization'")
    else:
        try:
            items = json.loads(out).get("items", [])
            not_ready, stale, revs = [], [], set()
            for it in items:
                name = it["metadata"]["name"]
                conds = {c["type"]: c for c in it.get("status", {}).get("conditions", [])}
                ready = conds.get("Ready", {}).get("status") == "True"
                if it.get("spec", {}).get("suspend"):
                    not_ready.append(f"{name}(suspended)")
                elif not ready:
                    not_ready.append(f"{name}: {conds.get('Ready', {}).get('message', '')[:60]}")
                rev = it.get("status", {}).get("lastAppliedRevision", "")
                if rev:
                    revs.add(rev.split(":")[-1][:8])
            if len(revs) > 1:
                stale = sorted(revs)
            ok = bool(items) and not not_ready and not stale
            detail = (f"{len(items)} Kustomizations Ready at {next(iter(revs)) if revs else '?'}"
                      if ok else
                      (f"not ready: {'; '.join(not_ready)}" if not_ready else "")
                      + (f" revisions diverge: {stale}" if stale else "")
                      + ("" if items else "no Kustomizations found"))
            yield (f"{P}:gitops", ok, f"{env} Flux Kustomizations", detail,
                   f"ssh bet@{cp1} '{KCTL} -n flux-system get kustomization'")
        except Exception as e:  # noqa: BLE001
            yield (f"{P}:gitops", False, f"{env} Flux Kustomizations",
                   f"parse failed: {str(e)[:80]}",
                   f"ssh bet@{cp1} '{KCTL} -n flux-system get kustomization'")

    # ---- Kyverno admission is live and the signing policy is Ready --------
    # A policy that exists but is not Ready, or an admission controller with 0
    # ready replicas with failurePolicy Fail, both mean "nothing can be admitted"
    # or "anything can be admitted" depending on the webhook config. Either is red.
    rc, out = _ssh(SSH, cp1,
                   f"{KCTL} -n kyverno get deploy kyverno-admission-controller "
                   "-o jsonpath='{.status.readyReplicas}'; echo; "
                   f"{KCTL} get clusterpolicy verify-ghcr-betv3-signed "
                   "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null", 40)
    lines = [ln.strip().strip("'") for ln in out.splitlines()] if rc == 0 else []
    adm = lines[0] if lines else ""
    pol = lines[1] if len(lines) > 1 else ""
    ok = rc == 0 and adm.isdigit() and int(adm) >= 1 and pol == "True"
    yield (f"{P}:admission", ok, f"{env} Kyverno image-signing gate",
           f"admission {adm or '0'} ready, policy Ready={pol or 'missing'}",
           f"ssh bet@{cp1} '{KCTL} -n kyverno get deploy; {KCTL} get clusterpolicy'")


def checks(SSH):
    for env, c in CLUSTERS.items():
        try:
            yield from _cluster_checks(SSH, env, c)
        except Exception as e:  # noqa: BLE001 -- fail closed per cluster
            yield (f"k8s:{env}:module", False, f"{env} watchdog module",
                   f"raised {type(e).__name__}: {e}",
                   "python3 -c 'import watchdog_k8s_envs'")


if __name__ == "__main__":
    SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/watchdog"),
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=8"]
    for key, ok, label, detail, _fix in checks(SSH):
        print(f"{'ok  ' if ok else 'DOWN'}  {key:24} {detail}")
