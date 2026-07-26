#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

./scripts/run_stage4n_ai_tests.sh
PYTHONPATH=backend/src python3 scripts/validate_stage4m_dependency_simulation.py
node scripts/validate_frontend_syntax.mjs
python3 scripts/validate_stage4_source.py
python3 scripts/validate_stage4_contracts.py
bash -n \
  scripts/run_stage4n_ai_tests.sh \
  scripts/run_stage4n_ai_simulator_e2e.sh \
  scripts/run_stage4n_full_gates.sh \
  scripts/start_stage4m_simulation.sh \
  scripts/run_stage4m_simulated_e2e.sh

echo "Stage 4N source gates passed."
