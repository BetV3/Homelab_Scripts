# Homelab Scripts

Operational scripts from a 7-host vSphere homelab running three RKE2
Kubernetes clusters, a fleet watchdog, and a self-hosted observability stack.

This repo exists because a portfolio full of numbers is not evidence. Every
file here is the actual artifact behind a claim made elsewhere, with internal
addressing replaced by placeholders. The logic is unmodified.

## Monitoring

| File | What it proves |
|---|---|
| `monitoring/watchdog_k8s_envs.py` | 16 signals across staging and production: API reachability through the VIP, node readiness, etcd quorum, CNI, VIP holder, ingress, real pod scheduling, backup freshness. Scheduling is verified by running a pod and reading its output, not by reading a status field. |
| `monitoring/watchdog_edge.py` | 4 signals for a Cloudflare-tunnel edge: connector health, tunnel connection count, public HTTP reachability, and the apex left on separate hosting. |
| `monitoring/watchdog_obs.py` | 5 signals for the observability host itself: container set, scrape target count, ingestion rate, HTTPS dashboard reachability, disk. |

## Tests that prove the monitoring fails correctly

A check that has never failed is a belief, not a control. Each of these
breaks the thing being watched and asserts the signal goes red for the
*right* reason.

| File | Cases |
|---|---|
| `tests/test_watchdog_new.py` | 11 red-run cases across the k8s and edge modules |
| `tests/test_watchdog_obs.py` | 14 red-run cases for the observability module |
| `tests/test_ssh_graded.py` | 9 cases separating "host is saturated" from "host is down" |

`test_ssh_graded.py` came from a real false positive. A build drove a 4-core
box to load 10.37; sshd could not complete a handshake inside the check's 25s
timeout, so a healthy host reported as unreachable. The fix retries once with
a 60s budget: answering slowly is a *latency* signal, not an outage.

## Backups

| File | What it proves |
|---|---|
| `backup/k8s_backup_envs.sh` | etcd snapshot plus the rebuild material for a cluster, into restic |
| `backup/k8s_restore_verify.sh` | restores into a scratch directory and asserts file sizes |

These carry a specific lesson. `node-token` in an RKE2 server directory is a
**symlink**. Archiving it with plain `tar cf` stores the link, not the target,
so every restore produced a 0-byte token while the backup job reported
success. The fix is `tar -ch` plus a hard size assertion. The verifier
previously printed `RESTORE VERIFIED` on the same run it reported
`0 bytes TOO-SMALL` — a checker that does not fail on its own failure is
worse than no checker.

## Metrics pipeline

`observability/prove_queue.sh` stops the metrics database for 100 seconds and
measures whether the agent's on-disk queue actually protects samples. Result:
queue grew 57 B to 7.7 MB, flushed on recovery, and every node reported the
same sample count across the outage window — zero loss.

## Conventions

- Internal addresses are replaced with `${PLACEHOLDER}` names or documented
  lab-subnet addresses. No credentials appear in any file; the originals read
  secrets from `0600` files on the host.
- Scripts are intended to be read as much as run. They carry comments
  explaining the failure that motivated them.
