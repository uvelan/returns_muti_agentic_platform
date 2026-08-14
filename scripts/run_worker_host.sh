#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER="${1:-}"
[[ -n "$WORKER" ]] || {
  echo "Usage: $0 {temporal|discovery|orchestrator|outbox|integration-outbox|housekeeping} [--validate-ai]" >&2
  exit 2
}
shift
validate_ai=false

while (($# > 0)); do
  case "$1" in
    --validate-ai)
      validate_ai=true
      ;;
    -h|--help)
      echo "Usage: $0 {temporal|discovery|orchestrator|outbox|integration-outbox|housekeeping} [--validate-ai]"
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

case "$WORKER" in
  temporal) SCRIPT=run_return_workflow_worker.py ;;
  # order-discovery is in REQUIRED_PROCESS_CLASSES: without it
  # GET /api/config/adoption never reaches LIVE, because a required class
  # that is never started can never report adoption.
  discovery) SCRIPT=run_order_discovery_worker.py ;;
  housekeeping) SCRIPT=run_housekeeping_worker.py ;;
  orchestrator) SCRIPT=run_return_orchestrator.py ;;
  outbox) SCRIPT=run_outbox_publisher.py ;;
  integration-outbox)
    cd "$ROOT/backend"
    export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
    if command -v poetry >/dev/null; then
      exec poetry run python -m return_platform.workers.integration_outbox
    fi
    exec "$(venv_python)" -m return_platform.workers.integration_outbox
    ;;
  *)
    echo "Usage: $0 {temporal|discovery|orchestrator|outbox|integration-outbox|housekeeping} [--validate-ai]" >&2
    exit 2
    ;;
esac

cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v poetry >/dev/null; then
  exec poetry run python "scripts/$SCRIPT"
fi
exec "$(venv_python)" "scripts/$SCRIPT"
