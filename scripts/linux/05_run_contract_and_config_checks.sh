#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT"
docker compose --profile containerized-app config --quiet
python3 "$LINUX_SCRIPT_DIR/verify_mandatory_routes.py" \
  "$LINUX_SCRIPT_DIR/mandatory_routes.json" \
  "$REPO_ROOT/frontend/src/routes.ts"
if command -v poetry >/dev/null 2>&1; then
  POETRY=(poetry)
elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
  POETRY=("$RUNTIME_ROOT/tooling/bin/poetry")
else
  echo "Poetry is required for contract validation." >&2
  exit 2
fi
cd "$REPO_ROOT/backend"
"${POETRY[@]}" run python ../scripts/validate_stage4_source.py
"${POETRY[@]}" run python ../scripts/validate_stage4_contracts.py
"${POETRY[@]}" run python ../scripts/validate_stage4m_dependency_simulation.py
"${POETRY[@]}" run python ../scripts/validate_stage4n_ai_gateway.py
"${POETRY[@]}" run python ../scripts/check_openapi_drift.py
cd "$REPO_ROOT"
git diff --check
