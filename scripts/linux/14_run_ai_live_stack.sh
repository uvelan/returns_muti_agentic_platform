#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
log="$EVIDENCE_DIR/ai-live-stack.log"

RETURN_PLATFORM_API="$API" "$REPO_ROOT/scripts/run_stage4n_live_stack_e2e.sh" | tee "$log"

curl --fail --silent --show-error "$API/api/v1/ai-gateway/routes" \
  --output "$EVIDENCE_DIR/ai-routes.json"
curl --fail --silent --show-error "$API/api/v1/ai-gateway/tasks" \
  --output "$EVIDENCE_DIR/ai-tasks.json"
curl --fail --silent --show-error "$API/api/v1/ai-gateway/metrics/summary" \
  --output "$EVIDENCE_DIR/ai-metrics-summary.json"
curl --fail --silent --show-error "$API/api/v1/dependency-simulator/summary" \
  --output "$EVIDENCE_DIR/dependency-simulator-summary.json"

jq -e '.data | type == "array" and length > 0' "$EVIDENCE_DIR/ai-routes.json" >/dev/null
jq -e '.data | type == "array" and length > 0' "$EVIDENCE_DIR/ai-tasks.json" >/dev/null
jq -e '.data != null' "$EVIDENCE_DIR/ai-metrics-summary.json" >/dev/null
jq -e '.data.ai != null' "$EVIDENCE_DIR/dependency-simulator-summary.json" >/dev/null
