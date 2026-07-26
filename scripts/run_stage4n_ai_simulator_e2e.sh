#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# This is a dependency-light E2E for the AI control plane. It uses scripted
# simulator providers and never calls a paid/live model endpoint.
PYTHONPATH=backend/src python3 scripts/validate_stage4n_ai_gateway.py
PYTHONPATH=backend/src pytest --noconftest -q \
  backend/tests/test_dependency_simulation.py \
  -k 'simulator_ai or ai_failure or configured_lightweight'

echo "Stage 4N simulator AI E2E passed."
