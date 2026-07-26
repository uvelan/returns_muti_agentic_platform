#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
log="$EVIDENCE_DIR/ai-live-stack.log"

cd "$REPO_ROOT/backend"
if command -v poetry >/dev/null 2>&1; then
  POETRY=(poetry)
elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
  POETRY=("$RUNTIME_ROOT/tooling/bin/poetry")
else
  echo "Poetry is required for safe live model probing." >&2
  exit 2
fi
PYTHONPATH="$REPO_ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}" \
  "${POETRY[@]}" run python "$REPO_ROOT/scripts/probe_configured_ai_models.py" \
  >"$EVIDENCE_DIR/ai-model-probe.json"
jq -e '
  .GOOGLE.configured == true
  and .GOOGLE.catalogStatus == 200
  and .GOOGLE.allConfiguredCredentialsWorking == true
  and .GOOGLE.configuredModelCount == 4
  and .GOOGLE.allConfiguredModelsWorking == true
  and ([.GOOGLE.modelResults[].model] | sort) == ([
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash"
  ] | sort)
  and .NVIDIA.configured == true
  and .NVIDIA.catalogStatus == 200
  and .NVIDIA.allConfiguredCredentialsWorking == true
  and .NVIDIA.configuredModelCount == 5
  and .NVIDIA.allConfiguredModelsWorking == true
  and ([.NVIDIA.modelResults[].model] | sort) == ([
    "meta/llama-3.2-3b-instruct",
    "meta/llama-3.1-8b-instruct",
    "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
    "nvidia/nemotron-3-nano-30b-a3b",
    "abacusai/dracarys-llama-3.1-70b-instruct"
  ] | sort)
' "$EVIDENCE_DIR/ai-model-probe.json" >/dev/null

cd "$REPO_ROOT"
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
