#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validate_ai=false
force_ai_validation=false
refresh_ai_routes=false

usage() {
  cat <<'EOF'
Usage:
  ./scripts/prepare_runtime_configuration.sh
  ./scripts/prepare_runtime_configuration.sh --validate-ai
  ./scripts/prepare_runtime_configuration.sh --force-ai-validation
  ./scripts/prepare_runtime_configuration.sh --refresh-ai-routes

Prepare SQL and Neo4j migrations, and the active graph configuration.

Normal preparation never calls an AI provider.
--validate-ai runs live validation only when the 24-hour interval has elapsed.
--force-ai-validation bypasses the interval and is operator-only.
--refresh-ai-routes publishes all configured routes without provider calls.
EOF
}

while (($# > 0)); do
  case "$1" in
    --validate-ai)
      validate_ai=true
      ;;
    --force-ai-validation)
      validate_ai=true
      force_ai_validation=true
      ;;
    --refresh-ai-routes)
      refresh_ai_routes=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$refresh_ai_routes" == "true" && "$validate_ai" == "true" ]]; then
  echo "--refresh-ai-routes cannot be combined with AI validation." >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Serialize preparation, on every host that can run this script.
#
# This used to be `command -v flock || exit 1`. Git Bash on Windows ships no
# `flock`, so `run_backend_host.sh` and `run_worker_host.sh` -- both of which
# call this -- could not start at all there, while `bootstrap_host.ps1`,
# `run_backend_host.ps1` and all of `scripts/windows/` advertise Windows
# support and `run_worker_host.sh` carries a comment specifically handling "Windows
# under Git Bash" for the venv path. A documented prerequisite that half the
# repo contradicts is a defect in the repo.
#
# `flock` is still preferred where it exists: the kernel releases its lock when
# the process dies, which no userspace scheme gets for free. The fallback is a
# `mkdir` mutex, atomic on every filesystem this runs on, with the holder's pid
# recorded so a crashed run can be detected rather than blocking the next one
# forever.
# ---------------------------------------------------------------------------
mkdir -p "$ROOT/.runtime"
LOCK_FILE="$ROOT/.runtime/prepare-runtime.lock"
LOCK_DIR="$ROOT/.runtime/prepare-runtime.lock.d"
LOCK_WAIT_SECONDS="${PLATFORM_PREPARE_LOCK_WAIT_SECONDS:-120}"

release_lock_dir() {
  rm -rf "$LOCK_DIR"
}

acquire_lock_without_flock() {
  local waited=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    local holder=""
    [[ -f "$LOCK_DIR/pid" ]] && holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"

    # A pid that no longer exists means the holder died mid-run. Without this
    # the directory outlives it and every later run blocks until the timeout.
    if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
      echo "Reclaiming a preparation lock left by pid $holder." >&2
      rm -rf "$LOCK_DIR"
      continue
    fi

    if (( waited >= LOCK_WAIT_SECONDS )); then
      echo "Timed out after ${LOCK_WAIT_SECONDS}s waiting for runtime configuration preparation" >&2
      echo "held by pid ${holder:-unknown}. Remove $LOCK_DIR if that process is gone." >&2
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done

  printf '%s' "$$" >"$LOCK_DIR/pid"
  trap release_lock_dir EXIT
}

if command -v flock >/dev/null; then
  exec 9>"$LOCK_FILE"
  flock 9
else
  acquire_lock_without_flock
fi

if command -v python3.13 >/dev/null; then
  ENV_PYTHON=(python3.13)
else
  ENV_PYTHON=(python3)
fi

"${ENV_PYTHON[@]}" \
  "$ROOT/scripts/linux/ensure_runtime_env_keys.py" \
  --env-file "$ROOT/.env" \
  --example-file "$ROOT/.env.example"

export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"

if command -v poetry >/dev/null; then
  PYTHON=(poetry --directory "$ROOT/backend" run python)
elif [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PYTHON=("$ROOT/backend/.venv/bin/python")
elif [[ -x "$ROOT/backend/.venv/Scripts/python.exe" ]]; then
  # Windows, under Git Bash. Without this the venv is invisible and the branch
  # below falls through to a bare `python3`, which is not the platform's
  # environment and fails on the first import.
  PYTHON=("$ROOT/backend/.venv/Scripts/python.exe")
else
  PYTHON=(python3)
fi

# SQL migrations run here, not only in compose's `runtime-configuration-init`.
# Until now this was the one preparation step the host path did not do, so a
# host-run platform got its SQL schema only as a side effect of that init
# container -- which is a backend *image*, and building it is what made
# `infra.sh start` build the backend just to start the datastores.
"${PYTHON[@]}" "$ROOT/scripts/apply_sql_migrations.py"
"${PYTHON[@]}" "$ROOT/scripts/apply_neo4j_migrations.py"

# NOT `--if-missing`. That flag returns as soon as any release is active,
# BEFORE comparing what is on disk to what is published -- so an edited
# ai_gateway.yaml, returns/production.yaml or dependency_simulation.yaml could
# never reach a running platform. Every restart printed
# `graph_configuration_status=EXISTING` and served the old release, which is
# indistinguishable from a change that did not work: an operator edits a prompt,
# restarts, sees the old behaviour, and concludes the edit was wrong.
#
# Without it the bootstrap compiles the configuration and compares it to the
# active release: identical payloads print `UNCHANGED` and publish nothing, so
# the common case costs one comparison and no release churn. A real change
# publishes, which is the point of running this before the backend starts.
bootstrap_args=()
if [[ "$refresh_ai_routes" == "true" ]]; then
  bootstrap_args+=(--refresh-ai-routes)
elif [[ "$force_ai_validation" == "true" ]]; then
  bootstrap_args+=(--force-ai-validation)
elif [[ "$validate_ai" == "true" ]]; then
  bootstrap_args+=(--validate-ai)
fi

"${PYTHON[@]}" \
  "$ROOT/scripts/bootstrap_graph_configuration.py" \
  ${bootstrap_args[@]+"${bootstrap_args[@]}"}
