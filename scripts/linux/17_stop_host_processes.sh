#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
for name in frontend worker-integration-outbox worker-jobs worker-outbox worker-orchestrator \
  worker-temporal backend; do
  stop_managed_process "$name"
done
