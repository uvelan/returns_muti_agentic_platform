#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER="${1:-}"
case "$WORKER" in
  temporal) SCRIPT=run_return_workflow_worker.py ;;
  orchestrator) SCRIPT=run_return_orchestrator.py ;;
  outbox) SCRIPT=run_outbox_publisher.py ;;
  jobs) SCRIPT=run_data_job_worker.py ;;
  *) echo "Usage: $0 {temporal|orchestrator|outbox|jobs}" >&2; exit 2 ;;
esac
cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v poetry >/dev/null; then
  exec poetry run python "scripts/$SCRIPT"
fi
exec .venv/bin/python "scripts/$SCRIPT"
