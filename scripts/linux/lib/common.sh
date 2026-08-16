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

# Vault comes back SEALED after every restart of its container, and nothing in
# this repository unseals it automatically. Sealed Vault does not look like
# sealed Vault: `PLATFORM_MONGO_DSN` keeps its `.env` sentinel of
# `mongodb://vault-resolved.invalid/...`, so all six workers crash-loop on DNS
# resolution of a host that does not exist and `backend` goes unhealthy. That
# reads as six unrelated bugs. It is one, and it is this.
#
# Echoes exactly one of:
#   UNKNOWN_NO_CURL | UNREACHABLE | UNINITIALIZED | SEALED | UNSEALED
# Never fails, so a caller can print the state without `set -e` aborting.
vault_seal_state() {
  local address health
  # A missing diagnostic tool is not a diagnosis. Reporting UNREACHABLE here
  # would turn "curl is not installed" into "your Vault is down".
  command -v curl >/dev/null 2>&1 || {
    printf 'UNKNOWN_NO_CURL\n'
    return 0
  }
  address="${PLATFORM_VAULT_ADDRESS:-http://127.0.0.1:8200}"
  address="${address%/}"
  # sealedcode/uninitcode make Vault answer 200 in states where it otherwise
  # answers 503/501, so a non-200 here really does mean "not reachable".
  health="$(
    curl --fail --silent --max-time 5 \
      "$address/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200" \
      2>/dev/null || true
  )"
  if [[ -z "$health" ]]; then
    printf 'UNREACHABLE\n'
    return 0
  fi
  if [[ "$health" == *'"initialized":false'* ]]; then
    printf 'UNINITIALIZED\n'
    return 0
  fi
  if [[ "$health" == *'"sealed":true'* ]]; then
    printf 'SEALED\n'
    return 0
  fi
  printf 'UNSEALED\n'
}

# One line, naming the fix. Returns non-zero unless Vault is usable.
assert_vault_unsealed() {
  local state
  state="$(vault_seal_state)"
  case "$state" in
    UNSEALED)
      return 0
      ;;
    UNKNOWN_NO_CURL)
      # Warn and pass. Blocking a startup on the absence of a probe would be a
      # check that fails closed against itself rather than against Vault.
      printf 'Vault seal state could not be checked: curl is not installed.\n' >&2
      return 0
      ;;
    SEALED)
      printf 'Vault is SEALED. Every credential resolves to the .env sentinel until it is opened; run: ./scripts/infra.sh unseal\n' >&2
      ;;
    UNINITIALIZED)
      printf 'Vault is UNINITIALIZED. Run: ./scripts/infra.sh unseal (it initializes, unseals and seeds).\n' >&2
      ;;
    UNREACHABLE)
      printf 'Vault is UNREACHABLE at %s. Start it with: ./scripts/infra.sh start\n' \
        "${PLATFORM_VAULT_ADDRESS:-http://127.0.0.1:8200}" >&2
      ;;
  esac
  return 1
}

# Resolve the Poetry executable into the global array POETRY_CMD. Non-zero when
# there is none.
#
# `$RUNTIME_ROOT/tooling/bin/poetry` was the only fallback five scripts knew
# about, and NOTHING IN THIS REPOSITORY HAS EVER CREATED IT.
# `scripts/bootstrap_host.sh` -- the documented first-time setup -- installs
# Poetry into `$REPO_ROOT/.tmp/poetry` and does not put it on PATH, so on a host
# set up exactly as documented every one of those branches missed and the phase
# exited 2 with "Poetry is required", next to a working Poetry it had installed
# itself. `.tmp/poetry/bin/poetry` is added here; the `tooling` path is kept
# because an environment that does provision it should keep working.
poetry_cmd() {
  if command -v poetry >/dev/null 2>&1; then
    POETRY_CMD=(poetry)
  elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
    POETRY_CMD=("$RUNTIME_ROOT/tooling/bin/poetry")
  elif [[ -x "$REPO_ROOT/.tmp/poetry/bin/poetry" ]]; then
    POETRY_CMD=("$REPO_ROOT/.tmp/poetry/bin/poetry")
  elif [[ -x "$REPO_ROOT/.tmp/poetry/Scripts/poetry.exe" ]]; then
    POETRY_CMD=("$REPO_ROOT/.tmp/poetry/Scripts/poetry.exe")
  else
    POETRY_CMD=()
    printf 'Poetry is unavailable. Run scripts/bootstrap_host.sh, which installs it into .tmp/poetry.\n' >&2
    return 2
  fi
}

# Resolve a command prefix that runs Python inside the backend environment, and
# set it in the global array BACKEND_PYTHON. Returns non-zero when there is none.
#
# Five scripts used to branch on `poetry` then on
# `$RUNTIME_ROOT/tooling/bin/poetry` and give up. Nothing in this repository has
# ever created that second path -- `bootstrap_host.sh` installs Poetry into
# `.tmp/poetry`, which is a third location, and does not add it to PATH. So on
# any host bootstrapped exactly as documented, both branches missed and the
# scripts exited 2 with "Poetry is required", having ignored the working
# `backend/.venv` that `bootstrap_host.sh` had just created.
#
# The stale branch is kept rather than removed: it costs one `-x` test, and an
# environment that does provision it keeps working.
backend_python() {
  if poetry_cmd 2>/dev/null; then
    BACKEND_PYTHON=("${POETRY_CMD[@]}" run python)
  elif [[ -x "$REPO_ROOT/backend/.venv/bin/python" ]]; then
    BACKEND_PYTHON=("$REPO_ROOT/backend/.venv/bin/python")
  elif [[ -x "$REPO_ROOT/backend/.venv/Scripts/python.exe" ]]; then
    # Windows, under Git Bash.
    BACKEND_PYTHON=("$REPO_ROOT/backend/.venv/Scripts/python.exe")
  else
    BACKEND_PYTHON=()
    printf 'No backend Python environment. Run scripts/bootstrap_host.sh (or phase 02).\n' >&2
    return 2
  fi
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
    # Redeploy/runtime preparation uses descriptor 9 for flock. Long-running
    # children must not inherit it or they keep the deployment lock forever.
    exec 9>&-
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

process_tree_postorder() {
  local root_pid="$1" child_pid
  while read -r child_pid; do
    [[ -n "$child_pid" ]] || continue
    process_tree_postorder "$child_pid"
  done < <(
    ps -eo pid=,ppid= |
      awk -v parent="$root_pid" '$2 == parent { print $1 }'
  )
  printf '%s\n' "$root_pid"
}

stop_managed_process() {
  local name="$1" pid_file="$PID_DIR/${name}.pid"
  [[ -s "$pid_file" ]] || return 0
  local pid process_id attempt alive
  local -a process_ids=()
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    mapfile -t process_ids < <(process_tree_postorder "$pid")
    for process_id in "${process_ids[@]}"; do
      kill "$process_id" 2>/dev/null || true
    done
    for attempt in {1..20}; do
      alive=false
      for process_id in "${process_ids[@]}"; do
        if kill -0 "$process_id" 2>/dev/null; then
          alive=true
        fi
      done
      [[ "$alive" == false ]] && break
      sleep 0.5
    done
    if [[ "$alive" == true ]]; then
      for process_id in "${process_ids[@]}"; do
        kill -KILL "$process_id" 2>/dev/null || true
      done
      sleep 1
      for process_id in "${process_ids[@]}"; do
        if kill -0 "$process_id" 2>/dev/null; then
          printf 'Process %s (PID %s) did not stop cleanly.\n' \
            "$name" "$process_id" >&2
          return 1
        fi
      done
    fi
  fi
  rm -f "$pid_file"
}
