#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT"
docker compose --profile containerized-app config --quiet
# `verify_mandatory_routes.py` and `mandatory_routes.json` were deleted in Wave
# G3. They asserted seventeen routes in `frontend/src/routes.ts`, a file Wave F4
# removed along with all seventy-six legacy routes. `validate_stage4_source.py`
# below now owns the frontend-route assertion, against the four-domain registry,
# so restating it here would be a second list to keep correct.
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
