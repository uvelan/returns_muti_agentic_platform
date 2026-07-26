#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q backend/src/return_platform backend/tests scripts
PYTHONPATH=backend/src pytest --noconftest -q \
  backend/tests/test_ai_gateway_policy.py \
  backend/tests/test_ai_gateway_routing.py \
  backend/tests/test_dependency_simulation.py
PYTHONPATH=backend/src python3 scripts/validate_stage4n_ai_gateway.py

echo "Stage 4N AI tests passed."
