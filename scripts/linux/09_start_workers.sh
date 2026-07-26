#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
for worker in temporal orchestrator outbox jobs integration-outbox; do
  start_managed_process "worker-$worker" "$REPO_ROOT/scripts/run_worker_host.sh" "$worker"
done
