#!/usr/bin/env bash
# VERIFY BY RESTORE. A backup that has never been restored is a belief.
# The Talos secrets were "backed up" too -- as 0-byte artifacts.
set -uo pipefail
export RESTIC_PASSWORD_FILE=/home/bet/.hermes/.restic_pw
export RESTIC_REPOSITORY="sftp:bet@${WATCHDOG_HOST}:/home/bet/restic-repo"
export RESTIC_PROGRESS_FPS=0
SFTP="ssh -i /home/bet/.ssh/elvis -o BatchMode=yes -o StrictHostKeyChecking=no bet@${WATCHDOG_HOST} -s sftp"
# NOTE: sftp.command must be passed as ONE argument. Unquoted, the shell splits
# it and restic reads "bet@..." as a subcommand ("unknown command").
R() { restic -o "sftp.command=$SFTP" "$@"; }
FAIL=0

for tag in k8s-stg k8s-prd; do
  echo "================ $tag"
  DEST=/tmp/restore-$tag
  rm -rf "$DEST"; mkdir -p "$DEST"
  R restore latest --tag "$tag" --target "$DEST" 2>&1 | tail -2

  TAR=$(find "$DEST" -name rke2-state.tar | head -1)
  if [ -z "$TAR" ]; then echo "  FAIL: no rke2-state.tar restored"; FAIL=1; continue; fi
  echo "  restored tar: $(stat -c%s "$TAR") bytes"

  # the snapshot itself
  SNAP=$(tar tf "$TAR" | grep 'db/snapshots/.\+' | head -1)
  if [ -n "$SNAP" ]; then echo "  etcd snapshot present: $SNAP"; else echo "  FAIL: no snapshot in tar"; FAIL=1; fi

  # the node-token: THE piece that makes a rebuild possible.
  # It is a SYMLINK on the node (node-token -> token), so a tar without -h
  # stores the link and this restores as 0 bytes. That must FAIL the check --
  # an earlier version printed "RESTORE VERIFIED" with a 0-byte token, which
  # is precisely the false assurance that lost the Talos cluster.
  if tar tf "$TAR" | grep -q 'node-token'; then
    SZ=$(tar xf "$TAR" -O rke2/node-token 2>/dev/null | wc -c)
    if [ "$SZ" -gt 50 ]; then
      echo "  node-token restored: $SZ bytes OK"
    else
      echo "  FAIL: node-token restored as $SZ bytes (symlink not dereferenced)"
      FAIL=1
    fi
  else
    echo "  FAIL: node-token missing -- cluster would be UNRECOVERABLE"; FAIL=1
  fi

  KC=$(find "$DEST" -name rke2-kubeconfig.yaml | head -1)
  if [ -n "$KC" ] && grep -q "client-certificate-data" "$KC"; then
    echo "  kubeconfig has admin cert: OK"
  else
    echo "  FAIL: kubeconfig missing/!valid"; FAIL=1
  fi
  rm -rf "$DEST"
done

echo
[ "$FAIL" = 0 ] && echo "RESTORE VERIFIED for both clusters" || echo "RESTORE VERIFICATION FAILED"
exit $FAIL
