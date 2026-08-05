#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validate_ai="${PLATFORM_VALIDATE_AI_ON_STARTUP:-false}"

usage() {
  cat <<'EOF'
Usage: ./scripts/prepare_runtime_configuration.sh [--validate-ai]

Prepare Vault, Neo4j migrations, and the active graph configuration.
Live AI provider/model validation runs only with --validate-ai.
EOF
}

while (($# > 0)); do
  case "$1" in
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
"${ENV_PYTHON[@]}" "$ROOT/scripts/linux/ensure_runtime_env_keys.py"   --env-file "$ROOT/.env"   --example-file "$ROOT/.env.example"

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
"${PYTHON[@]}" "$ROOT/scripts/apply_neo4j_migrations.py"

bootstrap_args=(--if-missing)
if [[ "${validate_ai,,}" == "true" ]]; then
  bootstrap_args+=(--validate-ai)
fi
"${PYTHON[@]}" "$ROOT/scripts/bootstrap_graph_configuration.py" "${bootstrap_args[@]}"
