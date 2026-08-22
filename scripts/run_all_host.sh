#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" == "Linux" ]]; then
  source "$(dirname "${BASH_SOURCE[0]}")/linux/enable_python_ca_compat.sh"
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/linux/lib/common.sh"
validate_ai=false
supervise=true
while (($# > 0)); do
  case "$1" in
    --validate-ai) validate_ai=true ;;
    # Start everything and RETURN, instead of blocking in the watch loop below.
    #
    # This exists because two callers -- `scripts/linux/reset_all.sh` and
    # `reset_docker_environment.sh --start-host` -- invoked this script
    # synchronously as one step of a longer sequence. It never returns, so in
    # `reset_all.sh` the graph build that follows it never ran: the reset
    # completed as far as "processes started", left Neo4j empty, and the copilot
    # then truthfully reported finding no orders. That is the exact failure the
    # header comment of `reset_all.sh` says the script was written to prevent.
    # Worse, the `EXIT` trap meant the Ctrl-C an operator eventually pressed
    # tore down everything the reset had just started.
    --no-supervise) supervise=false ;;
    -h|--help)
      echo "Usage: ./scripts/run_all_host.sh [--validate-ai] [--no-supervise]"
      echo "  --no-supervise  start the processes and return, leaving them running"
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
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

if [[ "$supervise" == true ]]; then
  printf '%s\n' "$$" >"$supervisor_pid_file"
  # Only the supervising form installs this. Under `--no-supervise` an `EXIT`
  # trap would stop every process the moment this script returned successfully,
  # which is the opposite of what the caller asked for.
  trap cleanup INT TERM EXIT
fi

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

if [[ "$supervise" == false ]]; then
  echo "Host processes started. They keep running after this script returns."
  echo "  Frontend: http://127.0.0.1:5173/"
  echo "  Backend:  http://127.0.0.1:8000/"
  echo "  Stop:     ./scripts/linux/17_stop_host_processes.sh"
  exit 0
fi

processes=(
  backend
  frontend
  worker-temporal
  worker-discovery
  worker-orchestrator
  worker-outbox
  worker-integration-outbox
  worker-housekeeping
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
