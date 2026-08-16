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
cd "$REPO_ROOT/backend"
# These are plain `python script.py` invocations, so the backend venv serves
# them as well as Poetry does. That matters: `bootstrap_host.sh` leaves Poetry
# off PATH, and this phase used to exit 2 on a correctly bootstrapped host.
backend_python
"${BACKEND_PYTHON[@]}" ../scripts/validate_stage4_source.py
"${BACKEND_PYTHON[@]}" ../scripts/validate_stage4_contracts.py
"${BACKEND_PYTHON[@]}" ../scripts/validate_stage4m_dependency_simulation.py
"${BACKEND_PYTHON[@]}" ../scripts/validate_stage4n_ai_gateway.py
"${BACKEND_PYTHON[@]}" ../scripts/check_openapi_drift.py
cd "$REPO_ROOT"
git diff --check
