#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
{
  echo "timestamp=$(utc_now)"
  echo "kernel=$(uname -a)"
  echo "commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
  echo "fingerprint=$(repo_fingerprint)"
} >"$EVIDENCE_DIR/failure-context.txt"
git -C "$REPO_ROOT" status --short >"$EVIDENCE_DIR/git-status.txt"
docker compose -f "$REPO_ROOT/compose.yaml" ps --all \
  >"$EVIDENCE_DIR/docker-compose-ps.txt" 2>&1 || true
docker compose -f "$REPO_ROOT/compose.yaml" logs --no-color --tail=300 \
  >"$EVIDENCE_DIR/docker-compose.log" 2>&1 || true
for pid_file in "$PID_DIR"/*.pid; do
  [[ -e "$pid_file" ]] || continue
  name="$(basename "$pid_file" .pid)"
  pid="$(cat "$pid_file")"
  printf '%s\t%s\t%s\n' "$name" "$pid" "$(kill -0 "$pid" 2>/dev/null && echo RUNNING || echo STOPPED)"
done >"$EVIDENCE_DIR/process-status.tsv"
if [[ -d "$REPO_ROOT/frontend/test-results" ]]; then
  tar --create --gzip --file "$EVIDENCE_DIR/playwright-failure-artifacts.tar.gz" \
    --directory "$REPO_ROOT/frontend" test-results
fi
echo "Failure evidence collected under $EVIDENCE_DIR"
