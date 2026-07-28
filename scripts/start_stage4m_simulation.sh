#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo "Create .env first: cp .env.example .env" >&2; exit 2; }
python3 "$ROOT/scripts/linux/validate_env.py" "$ROOT/.env" --simulation
export PLATFORM_ENVIRONMENT=development
export PLATFORM_OMC_DEPENDENCY_MODE=SIMULATED
export PLATFORM_PARCEL_DEPENDENCY_MODE=SIMULATED
export PLATFORM_FREIGHT_DEPENDENCY_MODE=SIMULATED
export PLATFORM_LSI_DEPENDENCY_MODE=SIMULATED
./scripts/infra.sh start
exec ./scripts/run_all_host.sh
