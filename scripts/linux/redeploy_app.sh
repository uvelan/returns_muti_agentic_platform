#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

install_dependencies=false
skip_frontend_build=false
validate_ai=false
application_ports=(8000 5173)

usage() {
  cat <<'EOF'
Usage: ./scripts/linux/redeploy_app.sh [options]

Rebuild and restart the Linux host application after source changes.
Infrastructure, Vault bootstrap, graph configuration, seed data, and AI
validation are not rerun.

Options:
  --install-dependencies  Synchronize locked backend and frontend dependencies.
  --skip-frontend-build  Restart without running the frontend production build.
  --validate-ai          Run live provider/model validation once before startup.
  -h, --help             Show this help.
EOF
}

while (($# > 0)); do
  case "$1" in
    --install-dependencies)
      install_dependencies=true
      ;;
    --skip-frontend-build)
      skip_frontend_build=true
      ;;
    --validate-ai)
      validate_ai=true
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

require_command flock
require_command curl
require_command npm

exec 9>"$RUNTIME_ROOT/redeploy-v2.lock"
if ! flock -n 9; then
  echo "Another application redeploy is already running." >&2
  exit 1
fi

if [[ "$install_dependencies" == true ]]; then
  echo "Synchronizing locked backend dependencies..."
  cd "$REPO_ROOT/backend"
  if command -v uv >/dev/null 2>&1; then
    # [dependency-groups].dev in backend/pyproject.toml covers pytest/ruff/mypy/etc.,
    # so --all-groups alone resolves the full dev toolchain from uv.lock.
    uv sync --project "$REPO_ROOT/backend" --all-groups --frozen
  elif command -v poetry >/dev/null 2>&1; then
    poetry install --sync --no-interaction
  elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
    "$RUNTIME_ROOT/tooling/bin/poetry" install --sync --no-interaction
  else
    echo "uv or Poetry is required to install backend dependencies." >&2
    exit 2
  fi

  echo "Synchronizing locked frontend dependencies..."
  cd "$REPO_ROOT/frontend"
  npm ci --ignore-scripts=false
fi

if [[ "$skip_frontend_build" != true ]]; then
  echo "Building the frontend..."
  cd "$REPO_ROOT/frontend"
  npm run build
fi

echo "Stopping application host processes..."
"$LINUX_SCRIPT_DIR/17_stop_host_processes.sh"
"$LINUX_SCRIPT_DIR/stop_application_ports.sh" "${application_ports[@]}"

echo "Verifying all application ports are closed before startup..."
"$LINUX_SCRIPT_DIR/stop_application_ports.sh" \
  --check-only "${application_ports[@]}"

echo "Preparing runtime configuration once..."
prepare_args=()
[[ "$validate_ai" == true ]] && prepare_args+=(--validate-ai)
"$REPO_ROOT/scripts/prepare_runtime_configuration.sh" "${prepare_args[@]}"
export PLATFORM_SKIP_RUNTIME_PREPARE=true

echo "Starting backend and workers..."
"$LINUX_SCRIPT_DIR/08_start_backend.sh"
"$LINUX_SCRIPT_DIR/09_start_workers.sh"

echo "Starting frontend..."
"$LINUX_SCRIPT_DIR/10_start_frontend.sh"

echo "Checking application processes and endpoints..."
"$LINUX_SCRIPT_DIR/11_validate_host_processes.sh"

worker_ready=false
for attempt in {1..30}; do
  if "$LINUX_SCRIPT_DIR/12_validate_worker_heartbeats.sh" \
    >"$LOG_DIR/redeploy-worker-readiness.log" 2>&1; then
    worker_ready=true
    break
  fi
  sleep 2
done

if [[ "$worker_ready" != true ]]; then
  echo "Workers did not report healthy heartbeats within 60 seconds." >&2
  echo "Inspect $LOG_DIR/redeploy-worker-readiness.log" >&2
  exit 1
fi

curl --fail --silent --show-error \
  http://127.0.0.1:8000/health/ready >/dev/null
curl --fail --silent --show-error \
  http://127.0.0.1:5173/ >/dev/null

echo "Application redeployed successfully."
echo "Frontend: http://127.0.0.1:5173/"
echo "Backend:  http://127.0.0.1:8000/"
