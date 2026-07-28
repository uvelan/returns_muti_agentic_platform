#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"

command -v curl >/dev/null || { echo "curl is required." >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required." >&2; exit 2; }
curl -fsS "$API/health/live" >/dev/null

# Run the production-v2 return workflow with simulated OMC, parcel, and LSI.
./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL

# Validate the dedicated AI operational endpoints.
echo "AI route health:"
curl -fsS "$API/api/v1/ai-gateway/routes" | jq '.data | map({routeId,provider,model,credentialId,tier,circuitState,requestsThisMinute})'

echo "AI task policies:"
curl -fsS "$API/api/v1/ai-gateway/tasks" | jq '.data | map({taskId,tier,promptVersion,fallbackStrategy,allowTierEscalation})'

echo "AI usage summary:"
curl -fsS "$API/api/v1/ai-gateway/metrics/summary" | jq '.data'

echo "Dependency simulator AI summary:"
curl -fsS "$API/api/v1/dependency-simulator/summary" | jq '.data.ai'
