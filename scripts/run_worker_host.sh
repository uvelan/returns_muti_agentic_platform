#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Same helper as `run_backend_host.sh`, and it has to be here too: both
# `exec "$(venv_python)"` lines below used to call a function that was only ever
# defined in that other file. On any host without Poetry on PATH -- which is
# every host bootstrapped by `bootstrap_host.sh`, since that installs Poetry
# into `.tmp/poetry` rather than onto PATH -- every worker died instantly with
# `venv_python: command not found`, and the managed-process wrapper reported
# only "worker-X failed to start".
#
# `bin/` on Linux and macOS, `Scripts/` on Windows under Git Bash.
venv_python() {
  if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
    printf '%s' "$ROOT/backend/.venv/bin/python"
  elif [[ -x "$ROOT/backend/.venv/Scripts/python.exe" ]]; then
    printf '%s' "$ROOT/backend/.venv/Scripts/python.exe"
  else
    # `exit` here would only leave the command substitution's subshell, so the
    # caller would go on to `exec ""`. Print nothing and report failure; the
    # caller turns an empty result into a real error.
    return 1
  fi
}

usage() {
  echo "Usage: $0 {temporal|discovery|orchestrator|outbox|integration-outbox|housekeeping} [--validate-ai]"
}

WORKER="${1:-}"

# Help and validation BEFORE any side effect. `--help` used to be accepted as
# the worker name: the script shifted it away, found no options, and went
# straight on to run `prepare_runtime_configuration.sh` -- which rewrites
# `.env` and applies the SQL and Neo4j migrations -- only to fall through to the
# `usage` branch and exit 2. Asking a script what it does should not migrate a
# database.
case "$WORKER" in
  -h | --help)
    usage
    exit 0
    ;;
  temporal | discovery | orchestrator | outbox | integration-outbox | housekeeping) ;;
  "")
    usage >&2
    exit 2
    ;;
  *)
    printf 'Unknown worker: %s\n' "$WORKER" >&2
    usage >&2
    exit 2
    ;;
esac

shift
validate_ai=false

while (($# > 0)); do
  case "$1" in
    --validate-ai)
      validate_ai=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "${PLATFORM_SKIP_RUNTIME_PREPARE:-false}" != "true" ]]; then
  prepare_args=()
  [[ "$validate_ai" == true ]] && prepare_args+=(--validate-ai)
  "$ROOT/scripts/prepare_runtime_configuration.sh" "${prepare_args[@]}"
fi

# Resolved once, at top level rather than inside a command substitution, so a
# missing environment aborts this script instead of only its subshell.
PYTHON_BIN=()
if ! command -v poetry >/dev/null; then
  resolved_python="$(venv_python)" || {
    echo "No backend Python environment: install Poetry or run scripts/bootstrap_host.sh." >&2
    exit 1
  }
  PYTHON_BIN=("$resolved_python")
fi

case "$WORKER" in
  temporal) SCRIPT=run_return_workflow_worker.py ;;
  # order-discovery is in REQUIRED_PROCESS_CLASSES: without it
  # GET /api/config/adoption never reaches LIVE, because a required class
  # that is never started can never report adoption.
  discovery) SCRIPT=run_order_discovery_worker.py ;;
  housekeeping) SCRIPT=run_housekeeping_worker.py ;;
  orchestrator) SCRIPT=run_return_orchestrator.py ;;
  outbox) SCRIPT=run_outbox_publisher.py ;;
  # No `*)` fallback: the worker name was validated above, before any side
  # effect. A second usage branch here would be a second list to keep correct.
  integration-outbox)
    cd "$ROOT/backend"
    export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
    if command -v poetry >/dev/null; then
      exec poetry run python -m return_platform.workers.integration_outbox
    fi
    exec "${PYTHON_BIN[@]}" -m return_platform.workers.integration_outbox
    ;;
esac

cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v poetry >/dev/null; then
  exec poetry run python "scripts/$SCRIPT"
fi
exec "${PYTHON_BIN[@]}" "scripts/$SCRIPT"
