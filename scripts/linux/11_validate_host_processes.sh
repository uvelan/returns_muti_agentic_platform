#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# Mirrors 09_start_workers.sh. worker-jobs went with the dead data-job-worker;
# worker-discovery is required for adoption to reach LIVE.
for name in backend frontend worker-temporal worker-discovery worker-orchestrator \
  worker-outbox worker-integration-outbox; do
  pid_file="$PID_DIR/${name}.pid"
  [[ -s "$pid_file" ]] || {
    echo "Missing PID file for $name." >&2
    exit 1
  }
  pid="$(cat "$pid_file")"
  kill -0 "$pid" 2>/dev/null || {
    echo "$name PID $pid is not running." >&2
    exit 1
  }
done
curl --fail --silent --show-error http://127.0.0.1:8000/health/live >/dev/null
curl --fail --silent --show-error http://127.0.0.1:5173/ >/dev/null
