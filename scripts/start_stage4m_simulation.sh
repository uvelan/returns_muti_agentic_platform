#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo "Create .env first: cp .env.example .env" >&2; exit 2; }
set -a
# shellcheck disable=SC1091
source .env
set +a
[[ "${PLATFORM_ENVIRONMENT:-development}" != "production" ]] || { echo "Dependency simulation is forbidden in production." >&2; exit 2; }
export PLATFORM_OMC_DEPENDENCY_MODE="${PLATFORM_OMC_DEPENDENCY_MODE:-SIMULATED}"
export PLATFORM_PARCEL_DEPENDENCY_MODE="${PLATFORM_PARCEL_DEPENDENCY_MODE:-SIMULATED}"
export PLATFORM_FREIGHT_DEPENDENCY_MODE="${PLATFORM_FREIGHT_DEPENDENCY_MODE:-SIMULATED}"
export PLATFORM_LSI_DEPENDENCY_MODE="${PLATFORM_LSI_DEPENDENCY_MODE:-SIMULATED}"
./scripts/infra.sh start
exec ./scripts/run_all_host.sh
