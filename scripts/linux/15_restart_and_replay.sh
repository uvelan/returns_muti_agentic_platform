#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/scenario_evidence.sh"

"$LINUX_SCRIPT_DIR/17_stop_host_processes.sh"
"$LINUX_SCRIPT_DIR/08_start_backend.sh"
"$LINUX_SCRIPT_DIR/09_start_workers.sh"
"$LINUX_SCRIPT_DIR/10_start_frontend.sh"
"$LINUX_SCRIPT_DIR/11_validate_host_processes.sh"
"$LINUX_SCRIPT_DIR/12_validate_worker_heartbeats.sh"

API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
curl --fail --silent --show-error "$API/health/ready" \
  --output "$EVIDENCE_DIR/restart-readiness.json"

cd "$REPO_ROOT/backend"
backend_python
"${BACKEND_PYTHON[@]}" scripts/seed_e2e_data.py \
  >"$EVIDENCE_DIR/restart-seed-status.json"
jq -e '.ready == true and (.validationErrors | length == 0)' \
  "$EVIDENCE_DIR/restart-seed-status.json" >/dev/null

for scenario in BRANCH_PARCEL OFFSITE_HEAVY; do
  run_scenario_with_evidence "$scenario" "restart-${scenario}" "$API"
done
