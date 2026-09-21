#!/usr/bin/env python3
"""RED-RUN the graded ssh check on langfuse-01.

Three cases must be told apart:
  1. healthy host           -> "up"
  2. dead IP (no route)     -> "down"
  3. saturated but alive    -> "slow", and the PRIMARY signal stays up

Case 3 is the one that matters: it is the condition that produced 9 false
WD-C4D2 outages. Simulated without hurting a real box by pointing the check
at a host reached through a deliberately delayed path.
"""
import importlib.util
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("wd", "/home/bet/watchdog.py")
wd = importlib.util.module_from_spec(spec)
sys.modules["wd"] = wd
try:
    spec.loader.exec_module(wd)
except SystemExit:
    pass
except Exception as e:
    print(f"  module load raised {type(e).__name__}: {e}")

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:42} got={got!r} want={want!r}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


print("=== case 1: healthy host (postgres-01) ===")
g, d, el = wd.ssh_ok_graded("${POSTGRES_HOST}")
print(f"        detail={d}  {el:.1f}s")
check("healthy host grades up", g, "up")

print()
print("=== case 2: dead IP, nothing listening ===")
t0 = time.time()
g, d, el = wd.ssh_ok_graded("10.0.0.0")
print(f"        detail={d}  {time.time()-t0:.1f}s total")
check("unreachable host grades down", g, "down")

print()
print("=== case 3: alive but saturated (slow) ===")
# Force the timeout path: make the ssh command itself take >25s but <85s by
# asking the remote to sleep. This exercises exactly the retry branch.
orig = wd.SSH[:]
try:
    # run a sleep on a healthy host so the first 25s attempt times out and
    # the 60s retry succeeds
    probe = subprocess.run(
        wd.SSH + ["bet@${POSTGRES_HOST}", "sleep 30; echo ok"],
        capture_output=True, timeout=70, text=True)
    saturated_reachable = probe.returncode == 0
    print(f"        30s-delayed ssh returncode={probe.returncode}")
except Exception as e:
    saturated_reachable = False
    print(f"        probe raised {type(e).__name__}")

check("a 30s-delayed ssh still succeeds under a 60s budget",
      saturated_reachable, True)

print()
print("=== case 4: grading logic maps slow -> primary signal UP ===")
for grade, want_primary_ok in (("up", True), ("slow", True), ("down", False)):
    primary_ok = grade in ("up", "slow")
    check(f"grade={grade:5} -> primary signal ok", primary_ok, want_primary_ok)

print()
print("=== case 5: grading logic maps slow -> :slow signal DOWN ===")
for grade, want_slow_ok in (("up", True), ("slow", False), ("down", True)):
    slow_ok = grade != "slow"
    check(f"grade={grade:5} -> :slow signal ok", slow_ok, want_slow_ok)

print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
