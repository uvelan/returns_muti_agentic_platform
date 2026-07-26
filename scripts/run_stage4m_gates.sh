#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q backend/src/return_platform backend/tests scripts
PYTHONPATH=backend/src pytest --noconftest -q \
  backend/tests/test_dependency_simulation.py \
  backend/tests/test_production_return_state.py
PYTHONPATH=backend/src python3 scripts/validate_stage4m_dependency_simulation.py
node scripts/validate_frontend_syntax.mjs
python3 scripts/validate_stage4_source.py
python3 scripts/validate_stage4_contracts.py
bash -n \
  scripts/start_stage4m_simulation.sh \
  scripts/run_stage4m_simulated_e2e.sh \
  scripts/run_stage4m_gates.sh
