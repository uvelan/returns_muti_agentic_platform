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

Prepare Vault, Neo4j migrations, and the active graph configuration.

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

command -v flock >/dev/null || {
  echo "flock is required to serialize runtime configuration preparation." >&2
  exit 1
}

LOCK_FILE="$ROOT/.runtime/prepare-runtime.lock"
mkdir -p "$ROOT/.runtime"
exec 9>"$LOCK_FILE"
flock 9

if command -v python3.13 >/dev/null; then
  ENV_PYTHON=(python3.13)
else
  ENV_PYTHON=(python3)
fi

"${ENV_PYTHON[@]}" \
  "$ROOT/scripts/linux/ensure_runtime_env_keys.py" \
  --env-file "$ROOT/.env" \
  --example-file "$ROOT/.env.example"

source "$ROOT/scripts/vault/export_runtime_vault_env.sh"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"

if command -v poetry >/dev/null; then
  PYTHON=(poetry --directory "$ROOT/backend" run python)
elif [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PYTHON=("$ROOT/backend/.venv/bin/python")
else
  PYTHON=(python3)
fi

"${PYTHON[@]}" "$ROOT/scripts/vault/bootstrap_local_vault.py"
# SQL migrations run here, not only in compose's `runtime-configuration-init`.
# Until now this was the one preparation step the host path did not do, so a
# host-run platform got its SQL schema only as a side effect of that init
# container -- which is a backend *image*, and building it is what made
# `infra.sh start` build the backend just to start the datastores.
"${PYTHON[@]}" "$ROOT/scripts/apply_sql_migrations.py"
"${PYTHON[@]}" "$ROOT/scripts/apply_neo4j_migrations.py"

bootstrap_args=(--if-missing)
if [[ "$refresh_ai_routes" == "true" ]]; then
  bootstrap_args+=(--refresh-ai-routes)
elif [[ "$force_ai_validation" == "true" ]]; then
  bootstrap_args+=(--force-ai-validation)
elif [[ "$validate_ai" == "true" ]]; then
  bootstrap_args+=(--validate-ai)
fi

"${PYTHON[@]}" \
  "$ROOT/scripts/bootstrap_graph_configuration.py" \
  "${bootstrap_args[@]}"
