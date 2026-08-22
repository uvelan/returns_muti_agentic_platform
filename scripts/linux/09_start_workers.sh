#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/enable_python_ca_compat.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# `jobs` is gone: the data-console package it imported was deleted, so the
# container crashed on import at every start. `discovery` is added because it
# is a REQUIRED_PROCESS_CLASS -- omitting it left adoption stuck ACTIVATING.
# `housekeeping` is here because every reclaimer the platform has lives in it,
# and it was the one worker no host path started -- so nothing ever expired an
# interception or reclaimed a spent graph generation. It is deliberately absent
# from REQUIRED_PROCESS_CLASSES, which means adoption reaches LIVE and
# /health/ready stays green with the reaper dead: nothing complains, and the
# only symptom is unbounded growth nobody is watching.
for worker in temporal discovery orchestrator outbox integration-outbox housekeeping; do
  start_managed_process "worker-$worker" "$REPO_ROOT/scripts/run_worker_host.sh" "$worker"
done
