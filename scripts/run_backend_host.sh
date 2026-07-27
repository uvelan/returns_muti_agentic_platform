#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/vault/export_runtime_vault_env.sh"
if [[ "${PLATFORM_SKIP_RUNTIME_PREPARE:-false}" != "true" ]]; then
  "$ROOT/scripts/prepare_runtime_configuration.sh"
fi
cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v poetry >/dev/null; then
  exec poetry run uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000
fi
exec .venv/bin/python -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000
