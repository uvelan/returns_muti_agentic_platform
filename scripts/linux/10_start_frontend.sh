#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
start_managed_process frontend "$REPO_ROOT/scripts/run_frontend_host.sh"
for attempt in {1..60}; do
  curl --fail --silent --show-error http://127.0.0.1:5173/ >/dev/null && exit 0
  sleep 1
done
echo "Frontend did not become reachable within 60 seconds." >&2
exit 1
