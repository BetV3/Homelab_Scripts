#!/usr/bin/env bash
# Back up the staging and prod k8s clusters: etcd snapshot + rebuild material.
#
# Generalised from k8s_backup.sh (dev/mgmt cluster). Runs on the ASSISTANT,
# which holds the restic key, pulling from each cluster's cp-01.
#
# BUG FOUND BY RESTORE-VERIFYING (2026-09-20): `node-token` is a SYMLINK to
# `token` in the same directory. Plain `tar cf` stored the LINK, so the restore
# produced a 0-byte node-token -- the single artifact that makes a rebuild
# possible was effectively absent while the backup reported success. Exactly
# the shape of the Talos secrets that were "backed up" as 0 bytes.
# Fixed with `tar -h` (dereference) and by archiving BOTH names.
#
# NOTE: this same bug exists in k8s_backup.sh for the dev cluster.
#
# FAILS LOUDLY on an empty artifact, and asserts the node-token has real bytes.
set -uo pipefail

KEY=/home/bet/.ssh/elvis
export RESTIC_PASSWORD_FILE=/home/bet/.hermes/.restic_pw
export RESTIC_REPOSITORY="sftp:bet@${WATCHDOG_HOST}:/home/bet/restic-repo"
export RESTIC_PROGRESS_FPS=0
SFTP="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=no bet@${WATCHDOG_HOST} -s sftp"
ts() { date -Is; }
RC=0

for pair in "stg:10.110.0.31" "prd:10.110.0.41"; do
  ENVN=${pair%%:*}; CP1=${pair##*:}
  STAGE="/home/bet/backups/k8s-$ENVN"
  echo "================ $ENVN ($CP1)"
  mkdir -p "$STAGE"; rm -rf "${STAGE:?}"/*

  echo "$(ts) on-demand etcd snapshot"
  ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no "bet@$CP1" \
    'sudo rke2 etcd-snapshot save --name hermes-nightly 2>&1 | tail -2' || { RC=1; continue; }

  echo "$(ts) pulling snapshot + cluster secrets"
  # -h dereferences symlinks: node-token -> token. Both names archived so a
  # restore works regardless of which one a rebuild reaches for.
  ssh -i "$KEY" -o BatchMode=yes "bet@$CP1" \
    'sudo tar -chf - -C /var/lib/rancher/rke2/server --transform "s|^|rke2/|" \
        db/snapshots node-token token 2>/dev/null; true' > "$STAGE/rke2-state.tar"
  ssh -i "$KEY" -o BatchMode=yes "bet@$CP1" 'sudo cat /etc/rancher/rke2/rke2.yaml' \
    > "$STAGE/rke2-kubeconfig.yaml"
  ssh -i "$KEY" -o BatchMode=yes "bet@$CP1" 'sudo cat /etc/rancher/rke2/config.yaml' \
    > "$STAGE/rke2-config.yaml"

  BAD=0
  for f in rke2-state.tar rke2-kubeconfig.yaml rke2-config.yaml; do
    sz=$(stat -c%s "$STAGE/$f" 2>/dev/null || echo 0)
    echo "$(ts)   $f: $sz bytes"
    [ "$sz" -lt 100 ] && { echo "$(ts) FATAL: $f empty/tiny"; BAD=1; }
  done

  if ! tar tf "$STAGE/rke2-state.tar" 2>/dev/null | grep -q 'db/snapshots/.\+'; then
    echo "$(ts) FATAL: no etcd snapshot inside the tar"; BAD=1
  else
    echo "$(ts)   etcd snapshots: $(tar tf "$STAGE/rke2-state.tar" | grep -c 'db/snapshots/.\+')"
  fi

  # THE assertion that the old script lacked: real bytes, not a dangling link.
  TOKSZ=$(tar xf "$STAGE/rke2-state.tar" -O rke2/node-token 2>/dev/null | wc -c)
  echo "$(ts)   node-token: $TOKSZ bytes"
  if [ "$TOKSZ" -lt 50 ]; then
    echo "$(ts) FATAL: node-token is empty -- the cluster would be UNRECOVERABLE"
    BAD=1
  fi

  [ "$BAD" = 0 ] || { RC=1; continue; }

  chmod 600 "$STAGE"/*
  echo "$(ts) restic backup --tag k8s-$ENVN"
  restic -o "sftp.command=$SFTP" backup "$STAGE" --tag "k8s-$ENVN" 2>&1 | tail -3 || RC=1
done

echo "$(ts) done rc=$RC"
exit $RC
