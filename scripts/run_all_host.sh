#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" == "Linux" ]]; then
  source "$(dirname "${BASH_SOURCE[0]}")/linux/enable_python_ca_compat.sh"
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()
cleanup() {
  local code=$?
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup INT TERM EXIT
start() {
  "$@" &
  PIDS+=("$!")
}
start "$ROOT/scripts/run_backend_host.sh"
start "$ROOT/scripts/run_worker_host.sh" temporal
start "$ROOT/scripts/run_worker_host.sh" orchestrator
start "$ROOT/scripts/run_worker_host.sh" outbox
start "$ROOT/scripts/run_worker_host.sh" jobs
start "$ROOT/scripts/run_worker_host.sh" integration-outbox
start "$ROOT/scripts/run_frontend_host.sh"
wait -n "${PIDS[@]}"
