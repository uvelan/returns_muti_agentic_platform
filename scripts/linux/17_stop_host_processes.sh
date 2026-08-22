#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# Reverse of 09_start_workers.sh's order. worker-jobs removed with the dead
# data-job-worker; worker-discovery added alongside it.
for name in frontend worker-housekeeping worker-integration-outbox worker-outbox worker-orchestrator \
  worker-discovery worker-temporal backend; do
  stop_managed_process "$name"
done
