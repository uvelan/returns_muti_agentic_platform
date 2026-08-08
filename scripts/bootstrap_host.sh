#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3.13 >/dev/null || { echo "Python 3.13 is required" >&2; exit 1; }
command -v node >/dev/null || { echo "Node.js 24 is required" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm 11 is required" >&2; exit 1; }
python3.13 - <<'PY'
import sys
assert sys.version_info[:2] == (3, 13), sys.version
PY
node -e 'const [major]=process.versions.node.split(".").map(Number); if (major !== 24) throw new Error(`Node 24 required, found ${process.versions.node}`)'
npm -v | awk -F. '$1 != 11 { print "npm 11 required, found " $0 > "/dev/stderr"; exit 1 }'
if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  echo "Created .env. Replace placeholder credentials before running services."
fi
python3.13 "$ROOT/scripts/linux/ensure_runtime_env_keys.py" --env-file "$ROOT/.env"
python3.13 "$ROOT/scripts/linux/ensure_local_infrastructure_secrets.py"
python3.13 "$ROOT/scripts/linux/ensure_local_replica_key.py"
cd "$ROOT/backend"
if command -v uv >/dev/null; then
  UV_CACHE="$ROOT/.tmp/uv-cache"
  mkdir -p "$UV_CACHE"
  # [dependency-groups].dev in backend/pyproject.toml covers pytest/ruff/mypy/etc.,
  # so --all-groups alone resolves the full dev toolchain from uv.lock.
  uv sync --project "$ROOT/backend" --all-groups --frozen --cache-dir "$UV_CACHE"
elif command -v poetry >/dev/null; then
  poetry env use python3.13
  poetry install --sync
else
  python3.13 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e .
  .venv/bin/python -m pip install \
    pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0 \
    ruff==0.15.21 mypy==2.3.0 'types-pyyaml>=6.0.12.20260518'
fi
cd "$ROOT/frontend"
npm ci
