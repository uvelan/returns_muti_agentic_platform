#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/enable_python_ca_compat.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# `jobs` is gone: the data-console package it imported was deleted, so the
# container crashed on import at every start. `discovery` is added because it
# is a REQUIRED_PROCESS_CLASS -- omitting it left adoption stuck ACTIVATING.
for worker in temporal discovery orchestrator outbox integration-outbox; do
  start_managed_process "worker-$worker" "$REPO_ROOT/scripts/run_worker_host.sh" "$worker"
done
