#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/enable_python_ca_compat.sh"
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
AI_MODEL_PROBE_MAX_ATTEMPTS="${AI_MODEL_PROBE_MAX_ATTEMPTS:-4}"
probe_passed=false
gate="$EVIDENCE_DIR/ai-model-gate.json"

for attempt in $(seq 1 "$AI_MODEL_PROBE_MAX_ATTEMPTS"); do
  echo "AI model probe attempt $attempt/$AI_MODEL_PROBE_MAX_ATTEMPTS"

  PYTHONPATH="$REPO_ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}" \
    "${POETRY[@]}" run python "$REPO_ROOT/scripts/probe_configured_ai_models.py" \
    >"$EVIDENCE_DIR/ai-model-probe.json"

  set +e
  PYTHONPATH="$REPO_ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}" \
    "${POETRY[@]}" run python "$REPO_ROOT/scripts/evaluate_ai_model_probe.py" \
    "$EVIDENCE_DIR/ai-model-probe.json" \
    --output "$gate"
  gate_rc=$?
  set -e

  if [[ "$gate_rc" -eq 0 ]]; then
    probe_passed=true
    echo "AI model gate passed on attempt $attempt"
    jq '{
      overallStatus,
      providerStatus:
        (.providerResults | with_entries(.value = .value.status)),
      tierCoverage,
      warnings
    }' "$gate"
    break
  fi

  echo "AI model probe attempt $attempt did not pass." >&2
  jq -r '
    .hardFailures[]? | "FAIL: \(.)",
    .warnings[]? | "WARN: \(.)"
  ' "$gate" >&2

  if (( attempt < AI_MODEL_PROBE_MAX_ATTEMPTS )); then
    delay=$((10 * (2 ** (attempt - 1)) + RANDOM % 5))
    echo "Retrying after $delay seconds..." >&2
    sleep "$delay"
  fi
done

if [[ "$probe_passed" != true ]]; then
  echo "AI model validation failed after $AI_MODEL_PROBE_MAX_ATTEMPTS attempts." >&2
  echo "Gate evidence: $gate" >&2
  exit 1
fi

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
