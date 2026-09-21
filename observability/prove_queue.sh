#!/usr/bin/env bash
# PROVE the queue works by BREAKING the write path.
# Stop VictoriaMetrics, let vmagent keep scraping, confirm bytes land in the
# on-disk queue, then bring VM back and confirm the buffered samples FLUSH.
#
# A persistent queue that has never buffered anything is a config line, not a
# control.
set -uo pipefail
S="ssh -i /home/bet/.ssh/elvis -o StrictHostKeyChecking=no"
H=bet@${OBS_HOST}

echo "=== baseline ==="
$S $H 'echo -n "  targets up: "; curl -s http://localhost:8429/api/v1/targets | python3 -c "
import json,sys;t=json.load(sys.stdin)[\"data\"][\"activeTargets\"];print(sum(1 for x in t if x.get(\"health\")==\"up\"),\"/\",len(t))"
echo -n "  queue bytes: "; sudo du -sb /opt/obs/vmagent-queue | cut -f1'

echo
echo "=== STOP VictoriaMetrics (write path broken) ==="
$S $H 'cd /opt/obs && sudo docker stop vm >/dev/null 2>&1; echo "  vm stopped"'

echo "  waiting 100s so several scrape cycles have nowhere to go..."
sleep 100

$S $H 'echo -n "  queue bytes now: "; sudo du -sb /opt/obs/vmagent-queue | cut -f1
echo -n "  vmagent still scraping: "; curl -s http://localhost:8429/api/v1/targets | python3 -c "
import json,sys;t=json.load(sys.stdin)[\"data\"][\"activeTargets\"];print(sum(1 for x in t if x.get(\"health\")==\"up\"),\"up\")"
echo "  pending data blocks:"; sudo find /opt/obs/vmagent-queue -type f | head -3 | sed "s/^/    /"'

echo
echo "=== RESTART VictoriaMetrics -- buffered samples must flush ==="
$S $H 'cd /opt/obs && sudo docker start vm >/dev/null 2>&1; echo "  vm started"'
sleep 75

$S $H 'echo -n "  queue bytes after flush: "; sudo du -sb /opt/obs/vmagent-queue | cut -f1
echo -n "  rows written last 5m: "
curl -s --data-urlencode "query=sum(increase(vm_rows_inserted_total[5m]))" http://localhost:8428/api/v1/query | python3 -c "
import json,sys;r=json.load(sys.stdin)[\"data\"][\"result\"];print(f\"{float(r[0][\"value\"][1]):,.0f}\" if r else 0)"
echo -n "  targets up: "; curl -s http://localhost:8429/api/v1/targets | python3 -c "
import json,sys;t=json.load(sys.stdin)[\"data\"][\"activeTargets\"];print(sum(1 for x in t if x.get(\"health\")==\"up\"),\"/\",len(t))"'

echo
echo "=== was there a HOLE in the data during the outage? ==="
$S $H 'curl -s --data-urlencode "query=count_over_time(up{job=\"node\"}[6m])" http://localhost:8428/api/v1/query | python3 -c "
import json,sys
r=json.load(sys.stdin)[\"data\"][\"result\"]
if not r: print(\"  no data\"); raise SystemExit
vals=[float(x[\"value\"][1]) for x in r]
print(f\"  samples per node over last 6m: min {min(vals):.0f} max {max(vals):.0f} (expect ~12 at 30s)\")
print(\"  -> buffered and flushed, no hole\" if min(vals)>=8 else \"  -> GAP: samples were lost\")"'
