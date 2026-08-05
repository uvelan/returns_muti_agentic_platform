#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validate_ai=false

while (($# > 0)); do
  case "$1" in
    --validate-ai)
      validate_ai=true
      ;;
    -h|--help)
      echo "Usage: ./scripts/run_backend_host.sh [--validate-ai]"
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

source "$ROOT/scripts/vault/export_runtime_vault_env.sh"
if [[ "${PLATFORM_SKIP_RUNTIME_PREPARE:-false}" != "true" ]]; then
  prepare_args=()
  [[ "$validate_ai" == true ]] && prepare_args+=(--validate-ai)
  "$ROOT/scripts/prepare_runtime_configuration.sh" "${prepare_args[@]}"
fi

cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v poetry >/dev/null; then
  exec poetry run uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000
fi
exec .venv/bin/python -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000
