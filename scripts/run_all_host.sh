#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" == "Linux" ]]; then
  source "$(dirname "${BASH_SOURCE[0]}")/linux/enable_python_ca_compat.sh"
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/linux/lib/common.sh"
validate_ai=false
while (($# > 0)); do
  case "$1" in
    --validate-ai) validate_ai=true ;;
    -h|--help)
      echo "Usage: ./scripts/run_all_host.sh [--validate-ai]"
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done
supervisor_pid_file="$PID_DIR/run-all-host.pid"
application_ports=(8000 5173)

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  "$LINUX_SCRIPT_DIR/17_stop_host_processes.sh" || true
  if [[ -s "$supervisor_pid_file" ]] \
    && [[ "$(cat "$supervisor_pid_file")" == "$$" ]]; then
    rm -f "$supervisor_pid_file"
  fi
  exit "$code"
}

if [[ -s "$supervisor_pid_file" ]]; then
  existing_supervisor="$(cat "$supervisor_pid_file")"
  if [[ "$existing_supervisor" =~ ^[0-9]+$ ]] \
    && kill -0 "$existing_supervisor" 2>/dev/null; then
    existing_command="$(
      tr '\0' ' ' <"/proc/$existing_supervisor/cmdline" 2>/dev/null || true
    )"
    existing_root="$(
      readlink -f "/proc/$existing_supervisor/cwd" 2>/dev/null || true
    )"
    if [[ "$existing_command" == *"run_all_host.sh"* ]] \
      && [[ "$existing_root" == "$ROOT" || "$existing_root" == "$ROOT/"* ]]; then
      echo "Stopping existing host supervisor PID $existing_supervisor."
      kill "$existing_supervisor"
      for attempt in {1..20}; do
        kill -0 "$existing_supervisor" 2>/dev/null || break
        sleep 0.5
      done
      if kill -0 "$existing_supervisor" 2>/dev/null; then
        echo "Existing host supervisor did not stop within 10 seconds." >&2
        exit 1
      fi
    fi
  fi
  rm -f "$supervisor_pid_file"
fi

printf '%s\n' "$$" >"$supervisor_pid_file"
trap cleanup INT TERM EXIT

"$LINUX_SCRIPT_DIR/17_stop_host_processes.sh"
"$LINUX_SCRIPT_DIR/stop_application_ports.sh" "${application_ports[@]}"
"$LINUX_SCRIPT_DIR/stop_application_ports.sh" \
  --check-only "${application_ports[@]}"

prepare_args=()
[[ "$validate_ai" == true ]] && prepare_args+=(--validate-ai)
"$ROOT/scripts/prepare_runtime_configuration.sh" "${prepare_args[@]}"
export PLATFORM_SKIP_RUNTIME_PREPARE=true

"$LINUX_SCRIPT_DIR/08_start_backend.sh"
"$LINUX_SCRIPT_DIR/09_start_workers.sh"
"$LINUX_SCRIPT_DIR/10_start_frontend.sh"
"$LINUX_SCRIPT_DIR/11_validate_host_processes.sh"

processes=(
  backend
  frontend
  worker-temporal
  worker-orchestrator
  worker-outbox
  worker-jobs
  worker-integration-outbox
)
while true; do
  for name in "${processes[@]}"; do
    pid_file="$PID_DIR/${name}.pid"
    if [[ ! -s "$pid_file" ]] || ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      echo "$name stopped; shutting down remaining host processes." >&2
      exit 1
    fi
  done
  sleep 2
done
