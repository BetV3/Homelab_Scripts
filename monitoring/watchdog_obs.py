#!/usr/bin/env python3
"""Watchdog checks for obs-01, the observability host.

Contract: checks(SSH) -> yields (key, ok, label, detail, fix).

WHY THIS EXISTS: an unmonitored monitoring system is exactly the shape of the
970 silent cron failures -- everything green at every step, producing nothing.
If obs-01 dies quietly, the first symptom is an empty dashboard during an
outage, which is the worst possible moment to discover it.

Three layers, and the third is the OUTPUT signal:
  obs:up            -- containers running          (process)
  obs:scrape-health -- targets actually being read (input)
  obs:ingest        -- ROWS ARE BEING WRITTEN      (output)

A scraper can hold every target "up" and still write nothing if the remote
write path is broken. Only obs:ingest can tell the difference.
"""
from __future__ import annotations

import json
import subprocess

OBS = "${OBS_HOST}"
# Compose service/container names. vmware-exporter added 2026-09-21.
WANT = {"vm", "vmagent", "grafana", "blackbox", "vmware-exporter"}
MIN_TARGETS = 60          # 76 live; alert well before a whole job vanishes
MAX_DOWN = 4              # a couple of flaps is noise, not an outage


def _ssh(SSH, host, cmd, timeout=60):
    try:
        p = subprocess.run(SSH + [f"bet@{host}", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"ssh-error: {e}"


def checks(SSH):
    # ---- 1. containers up ----------------------------------------------
    rc, out = _ssh(SSH, OBS, "sudo docker ps --format '{{.Names}}'", 60)
    names = {n.strip() for n in out.splitlines() if n.strip()}
    missing = WANT - names
    yield ("obs:up", rc == 0 and not missing,
           "observability containers running",
           "all 4 up" if not missing and rc == 0
           else f"missing: {','.join(sorted(missing)) or out[:60]}",
           f"ssh bet@{OBS} 'cd /opt/obs && sudo docker compose up -d'")

    # ---- 2. scrape health (INPUT) --------------------------------------
    rc, out = _ssh(
        SSH, OBS,
        "curl -s --max-time 20 http://localhost:8429/api/v1/targets "
        "| python3 -c \"import json,sys;d=json.load(sys.stdin);"
        "t=d['data']['activeTargets'];"
        "print(len(t), sum(1 for x in t if x.get('health')!='up'))\"", 90)
    ok, detail = False, f"query failed: {out[:70]}"
    if rc == 0 and out.split():
        try:
            total, down = (int(x) for x in out.split()[:2])
            ok = total >= MIN_TARGETS and down <= MAX_DOWN
            detail = f"{total} targets, {down} down"
        except Exception:  # noqa: BLE001
            detail = f"parse failed: {out[:70]}"
    yield ("obs:scrape-health", ok, "vmagent scrape targets healthy", detail,
           f"ssh bet@{OBS} 'curl -s localhost:8429/api/v1/targets'")

    # ---- 3. INGEST (OUTPUT) --------------------------------------------
    # Not "is the TSDB up" but "did it accept new rows in the last 10 min".
    # This is the signal that would catch a silently-broken write path.
    rc, out = _ssh(
        SSH, OBS,
        "curl -s --max-time 20 --data-urlencode "
        "'query=sum(increase(vm_rows_inserted_total[10m]))' "
        "http://localhost:8428/api/v1/query "
        "| python3 -c \"import json,sys;r=json.load(sys.stdin)['data']['result'];"
        "print(r[0]['value'][1] if r else 0)\"", 90)
    ok, detail = False, f"query failed: {out[:70]}"
    if rc == 0 and out.strip():
        try:
            rows = float(out.strip())
            ok = rows > 0
            detail = (f"{rows:,.0f} rows written in 10m" if ok
                      else "NO ROWS WRITTEN in 10m -- scraping but storing nothing")
        except Exception:  # noqa: BLE001
            detail = f"parse failed: {out[:70]}"
    yield ("obs:ingest", ok, "VictoriaMetrics ingesting samples", detail,
           f"ssh bet@{OBS} 'sudo docker logs vmagent --tail 40'")

    # ---- 4. grafana answering ------------------------------------------
    # HTTPS since 2026-09-21 (step-ca cert). -k because this hits localhost
    # while the cert names host.example.internal; the point of this
    # signal is "is Grafana serving", and cert VALIDITY is covered
    # separately and better by the blackbox probe_ssl_earliest_cert_expiry
    # series on the Network dashboard.
    rc, out = _ssh(
        SSH, OBS,
        "curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "
        "https://localhost:3000/api/health", 60)
    yield ("obs:grafana", out.strip() == "200", "grafana responding",
           f"HTTPS {out.strip() or 'no response'}",
           f"ssh bet@{OBS} 'sudo docker logs grafana --tail 40'")

    # ---- 5. TSDB disk --------------------------------------------------
    rc, out = _ssh(SSH, OBS,
                   "df --output=pcent /opt/obs | tail -1 | tr -dc '0-9'", 60)
    ok, detail = False, f"df failed: {out[:60]}"
    if rc == 0 and out.strip().isdigit():
        pct = int(out.strip())
        ok = pct < 85
        detail = f"{pct}% used of 116G"
    yield ("obs:disk", ok, "obs-01 TSDB disk", detail,
           f"ssh bet@{OBS} 'du -sh /opt/obs/vm'")


if __name__ == "__main__":
    # NOTE: langfuse-01 deliberately holds NO ~/.ssh/elvis -- it has only
    # ~/.ssh/watchdog. Running this standalone with the wrong identity made
    # all 5 signals fail closed on a HEALTHY host and looked like a real
    # outage. Prefer the watchdog key when it exists; that is the identity
    # watchdog.py actually passes in via checks(SSH).
    import os
    ident = os.path.expanduser("~/.ssh/watchdog")
    if not os.path.exists(ident):
        ident = os.path.expanduser("~/.ssh/elvis")
    S = ["ssh", "-i", ident, "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    for k, ok, label, detail, fix in checks(S):
        print(f"{'ok  ' if ok else 'DOWN'}  {k:22} {detail}")
