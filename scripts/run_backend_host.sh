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

# The venv layout differs by platform: `bin/` on Linux and macOS, `Scripts/` on
# Windows, where these scripts run under Git Bash. Hardcoding `bin/python` made
# the no-Poetry fallback resolve to nothing there, and bash reports a missing
# interpreter as exit 127 -- "command not found" -- with no output at all.
venv_python() {
  if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
    printf '%s' "$ROOT/backend/.venv/bin/python"
  elif [[ -x "$ROOT/backend/.venv/Scripts/python.exe" ]]; then
    printf '%s' "$ROOT/backend/.venv/Scripts/python.exe"
  else
    # `exit` here would leave only the command substitution's subshell, after
    # which the caller would `exec ""`. Report failure and let the caller abort.
    return 1
  fi
}

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
resolved_python="$(venv_python)" || {
  echo "No backend Python environment: install Poetry or run scripts/bootstrap_host.sh." >&2
  exit 1
}
exec "$resolved_python" -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000
