#!/usr/bin/env python3
"""Watchdog checks for the public edge: Cloudflare tunnel -> prod ingress.

Contract: checks(SSH) -> yields (key, ok, label, detail, fix).

WHY EACH SIGNAL EXISTS

A tunnel reporting `status: healthy` only means a connector attached. It says
NOTHING about whether the hostname reaches a live origin -- exactly the
"green at every step, producing nothing" shape as the 970 silent cron
failures. So there are three layers, and the OUTPUT one is the hostname:

  edge:tunnel-conns  - connector attached at all (infrastructure)
  edge:connector     - cloudflared running on the node (process)
  edge:public-http   - the hostname answers from the PUBLIC internet (OUTPUT)

edge:public-http is the only one that proves a visitor can load the site.

Also watched: the apex. example.com is the live job-hunt portfolio on AWS
S3+CloudFront and is deliberately NOT behind this tunnel yet. If it ever
breaks -- or silently starts resolving somewhere unexpected -- that matters
more than anything else here.

Fails CLOSED. Cloudflare blocks urllib's default UA with a 1010, so a real
UA is mandatory or every check would false-alarm.
"""
from __future__ import annotations

import json
import os
import subprocess

TUNNEL_CREDS = "/home/bet/.hermes/.k8s-prod-tunnel.json"
CF_ENV = "/home/bet/.hermes/cloudflare.env"
CONNECTOR_NODE = "10.110.0.41"
PUBLIC_HOST = "portfolio.example.com"
APEX = "example.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) watchdog-edge/1.0"

FIX = (f"ssh bet@{CONNECTOR_NODE} 'systemctl status cloudflared; "
       "journalctl -u cloudflared -n 40'")


def _sh(cmd, timeout=40):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _ssh(SSH, host, cmd, timeout=40):
    try:
        p = subprocess.run(SSH + [f"bet@{host}", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _cf_env():
    env = {}
    with open(CF_ENV) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def checks(SSH):
    # ---- 1. tunnel connections (API) ------------------------------------
    try:
        tid = json.load(open(TUNNEL_CREDS))["TunnelID"]
        env = _cf_env()
        rc, out = _sh(
            f'curl -s --max-time 20 -H "Authorization: Bearer '
            f'{env["CLOUDFLARE_API_TOKEN"]}" '
            f'"https://api.cloudflare.com/client/v4/accounts/'
            f'{env["CLOUDFLARE_ACCOUNT_ID"]}/cfd_tunnel/{tid}"')
        d = json.loads(out)
        if not d.get("success"):
            yield ("edge:tunnel-conns", False, "CF tunnel connections",
                   f"API error: {str(d.get('errors'))[:90]}", FIX)
        else:
            t = d["result"]
            n = len(t.get("connections") or [])
            yield ("edge:tunnel-conns", n > 0, "CF tunnel connections",
                   f"{t.get('name')} status={t.get('status')} connections={n}"
                   + ("" if n else "  -- healthy but NOTHING ATTACHED"), FIX)
    except Exception as e:  # noqa: BLE001
        yield ("edge:tunnel-conns", False, "CF tunnel connections",
               f"check failed: {type(e).__name__}: {e}", FIX)

    # ---- 2. connector process -------------------------------------------
    rc, out = _ssh(SSH, CONNECTOR_NODE, "systemctl is-active cloudflared", 25)
    yield ("edge:connector", rc == 0 and out.strip() == "active",
           "cloudflared connector",
           f"cloudflared is {out.strip() or 'unreachable'} on {CONNECTOR_NODE}",
           FIX)

    # ---- 3. OUTPUT SIGNAL: the hostname answers publicly -----------------
    # <500 is the bar, not ==200: 404 means our nginx answered (no ingress
    # rule yet) which still proves tunnel->origin works. 502/503 means the
    # origin is dead, and that is what must page.
    rc, out = _sh(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 25 '
                  f'-A "{UA}" https://{PUBLIC_HOST}/')
    code = (out or "000").strip()
    ok = code.isdigit() and 200 <= int(code) < 500
    yield ("edge:public-http", ok, f"{PUBLIC_HOST} reachable",
           f"HTTP {code}" + ("" if ok else "  -- origin unreachable through the tunnel"),
           FIX)

    # ---- 4. the apex: live job-hunt site, NOT behind this tunnel --------
    rc, out = _sh(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 25 '
                  f'-A "{UA}" https://{APEX}/')
    code = (out or "000").strip()
    ok = code == "200"
    yield ("edge:apex", ok, f"{APEX} (AWS, live portfolio)",
           f"HTTP {code}" + ("" if ok else "  -- THE LIVE PORTFOLIO IS DOWN"),
           "check AWS S3/CloudFront; this is NOT served by the k8s tunnel")


if __name__ == "__main__":
    SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/watchdog"),
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=8"]
    for key, ok, label, detail, _fix in checks(SSH):
        print(f"{'ok  ' if ok else 'DOWN'}  {key:20} {detail}")
