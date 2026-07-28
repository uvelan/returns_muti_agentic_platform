#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v flock >/dev/null || {
  echo "flock is required to serialize runtime configuration preparation." >&2
  exit 1
}
LOCK_FILE="$ROOT/.runtime/prepare-runtime.lock"
mkdir -p "$ROOT/.runtime"
exec 9>"$LOCK_FILE"
flock 9

source "$ROOT/scripts/vault/export_runtime_vault_env.sh"
PYTHONPATH_VALUE="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH="$PYTHONPATH_VALUE"

if command -v poetry >/dev/null; then
  PYTHON=(poetry --directory "$ROOT/backend" run python)
elif [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PYTHON=("$ROOT/backend/.venv/bin/python")
else
  PYTHON=(python3)
fi

"${PYTHON[@]}" "$ROOT/scripts/vault/bootstrap_local_vault.py"
"${PYTHON[@]}" "$ROOT/scripts/apply_neo4j_migrations.py"
"${PYTHON[@]}" "$ROOT/scripts/bootstrap_graph_configuration.py" --if-missing
