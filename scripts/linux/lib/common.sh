#!/usr/bin/env bash
set -euo pipefail

LINUX_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$LINUX_SCRIPT_DIR/../.." && pwd)"
RUNTIME_ROOT="$REPO_ROOT/.runtime/linux-validation"
STATE_DIR="$RUNTIME_ROOT/state"
LOG_DIR="$RUNTIME_ROOT/logs"
EVIDENCE_DIR="$RUNTIME_ROOT/evidence"
PID_DIR="$RUNTIME_ROOT/pids"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$EVIDENCE_DIR" "$PID_DIR"

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Required command is unavailable: %s\n' "$1" >&2
    return 2
  }
}

repo_fingerprint() {
  {
    git -C "$REPO_ROOT" rev-parse HEAD
    git -C "$REPO_ROOT" diff --binary HEAD
    git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=all
  } | sha256sum | awk '{print $1}'
}

write_phase_receipt() {
  local phase="$1" status="$2" exit_code="$3" started="$4" ended="$5"
  local output="$EVIDENCE_DIR/${phase}.json"
  python3 - "$output" "$phase" "$status" "$exit_code" "$started" "$ended" \
    "$(git -C "$REPO_ROOT" rev-parse HEAD)" "$(repo_fingerprint)" <<'PY'
import json
import pathlib
import sys

path, phase, status, exit_code, started, ended, commit, fingerprint = sys.argv[1:]
payload = {
    "schemaVersion": 1,
    "environment": "linux",
    "phase": phase,
    "status": status,
    "exitCode": int(exit_code),
    "startedAt": started,
    "endedAt": ended,
    "commit": commit,
    "treeFingerprint": fingerprint,
}
pathlib.Path(path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

run_and_record() {
  local phase="$1"
  shift
  local started ended exit_code status log
  started="$(utc_now)"
  log="$LOG_DIR/${phase}.log"
  set +e
  "$@" >"$log" 2>&1
  exit_code=$?
  set -e
  ended="$(utc_now)"
  status="PASS"
  if ((exit_code != 0)); then
    status="FAIL"
  fi
  write_phase_receipt "$phase" "$status" "$exit_code" "$started" "$ended"
  if ((exit_code == 0)); then
    printf '%s\n' "$(repo_fingerprint)" >"$STATE_DIR/${phase}.sha256"
  fi
  return "$exit_code"
}

checkpoint_valid() {
  local phase="$1" checkpoint="$STATE_DIR/${phase}.sha256"
  [[ -z "$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=all)" ]] \
    && [[ -s "$checkpoint" ]] \
    && [[ "$(cat "$checkpoint")" == "$(repo_fingerprint)" ]] \
    && python3 - "$EVIDENCE_DIR/${phase}.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
raise SystemExit(0 if path.is_file() and json.loads(path.read_text())["status"] == "PASS" else 1)
PY
}

start_managed_process() {
  local name="$1"
  shift
  local pid_file="$PID_DIR/${name}.pid" log="$LOG_DIR/${name}.log"
  if [[ -s "$pid_file" ]]; then
    local existing
    existing="$(cat "$pid_file")"
    if kill -0 "$existing" 2>/dev/null; then
      printf '%s already running as PID %s\n' "$name" "$existing"
      return 0
    fi
    rm -f "$pid_file"
  fi
  (
    cd "$REPO_ROOT"
    exec "$@"
  ) >"$log" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$pid_file"
  sleep 1
  kill -0 "$pid" 2>/dev/null || {
    printf '%s failed to start; inspect %s\n' "$name" "$log" >&2
    return 1
  }
}

stop_managed_process() {
  local name="$1" pid_file="$PID_DIR/${name}.pid"
  [[ -s "$pid_file" ]] || return 0
  local pid
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    local attempt
    for attempt in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      printf 'Process %s (PID %s) did not stop cleanly.\n' "$name" "$pid" >&2
      return 1
    fi
  fi
  rm -f "$pid_file"
}
