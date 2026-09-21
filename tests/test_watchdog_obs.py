#!/usr/bin/env python3
"""RED-RUN PROOF for watchdog_obs.py.

A signal that has never gone red for the right reason is a belief, not a
control. Each case breaks ONE input and asserts the specific signal flips.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "wo", "/home/bet/.hermes/cache/blocked-scripts/watchdog_obs.py")
wo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wo)


def fake(mapping):
    """Return an _ssh stub that answers by substring match on the command."""
    def _s(SSH, host, cmd, timeout=60):
        for needle, resp in mapping.items():
            if needle in cmd:
                return resp
        return (1, "")
    return _s


CASES = []


def case(name, key, mapping, want_ok):
    CASES.append((name, key, mapping, want_ok))


# --- obs:up
case("all containers present", "obs:up",
     {"docker ps": (0, "vm\nvmagent\ngrafana\nblackbox\nvmware-exporter")}, True)
case("grafana container GONE", "obs:up",
     {"docker ps": (0, "vm\nvmagent\nblackbox\nvmware-exporter")}, False)
case("vmware-exporter GONE", "obs:up",
     {"docker ps": (0, "vm\nvmagent\ngrafana\nblackbox")}, False)
case("docker daemon dead", "obs:up",
     {"docker ps": (1, "Cannot connect to the Docker daemon")}, False)

# --- obs:scrape-health
case("74 targets, 0 down", "obs:scrape-health",
     {"activeTargets": (0, "74 0")}, True)
case("whole job vanished (30 targets)", "obs:scrape-health",
     {"activeTargets": (0, "30 0")}, False)
case("many targets down", "obs:scrape-health",
     {"activeTargets": (0, "74 19")}, False)
case("targets api unreachable", "obs:scrape-health",
     {"activeTargets": (1, "connection refused")}, False)

# --- obs:ingest  (THE output signal)
case("rows being written", "obs:ingest",
     {"vm_rows_inserted_total": (0, "412000")}, True)
case("SCRAPING BUT STORING NOTHING", "obs:ingest",
     {"vm_rows_inserted_total": (0, "0")}, False)
case("tsdb query fails", "obs:ingest",
     {"vm_rows_inserted_total": (1, "curl: (7)")}, False)

# --- obs:grafana
case("grafana 200", "obs:grafana", {"api/health": (0, "200")}, True)
case("grafana 502", "obs:grafana", {"api/health": (0, "502")}, False)

# --- obs:disk
case("disk 45%", "obs:disk", {"df --output": (0, "45")}, True)
case("disk 91% FULL", "obs:disk", {"df --output": (0, "91")}, False)

fails = 0
print("=" * 62)
for name, key, mapping, want_ok in CASES:
    wo._ssh = fake(mapping)
    got = None
    for k, ok, label, detail, fix in wo.checks([]):
        if k == key:
            got = (ok, detail)
            break
    if got is None:
        print(f"  ERROR  {name}: signal {key} never yielded")
        fails += 1
        continue
    ok, detail = got
    good = (ok == want_ok)
    print(f"  {'PASS' if good else 'FAIL'}  {key:20} "
          f"{'green' if ok else 'RED  '}  {name}")
    if not good:
        print(f"         wanted ok={want_ok} got ok={ok} | {detail}")
        fails += 1

print("=" * 62)
print(f"{len(CASES)-fails}/{len(CASES)} correct")
sys.exit(1 if fails else 0)
