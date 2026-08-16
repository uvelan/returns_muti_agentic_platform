#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Pinned so a host bootstrap and a container build use the same Poetry; keep in
# step with POETRY_VERSION in backend/Dockerfile.
POETRY_VERSION=2.4.1
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
# Poetry is the only packaging tool, and `poetry.lock` the only lockfile. If
# Poetry is missing we install it rather than falling back to a hand-written
# `pip install pytest==... ruff==...` line: that line was a second declaration
# of the dev toolchain, and nothing kept it in step with the lockfile.
if command -v poetry >/dev/null; then
  POETRY=poetry
else
  # Its own venv rather than `pip install --user`: most 3.13 distributions mark
  # the system environment externally managed (PEP 668) and refuse the latter.
  if [[ ! -x "$ROOT/.tmp/poetry/bin/poetry" ]]; then
    python3.13 -m venv "$ROOT/.tmp/poetry"
    "$ROOT/.tmp/poetry/bin/python" -m pip install --quiet --upgrade pip
    "$ROOT/.tmp/poetry/bin/python" -m pip install --quiet "poetry==$POETRY_VERSION"
  fi
  POETRY="$ROOT/.tmp/poetry/bin/poetry"
fi
# Put the virtualenv at `backend/.venv`, and record the choice locally so every
# later `poetry` invocation agrees.
#
# Poetry's default is a venv in its own cache directory, keyed by a hash of the
# project path. Nothing in this repository can find that. Meanwhile
# `run_backend_host.sh`, `run_worker_host.sh`, `prepare_runtime_configuration.sh`
# and `reset_all.sh` all fall back to `backend/.venv` when `poetry` is not on
# PATH -- and it is not on PATH, because the branch above deliberately installs
# it into `.tmp/poetry`. On a genuinely clean machine that combination meant
# bootstrap reported success and then nothing could start, with
# "No backend Python environment" as the only clue.
#
# `--local` writes `backend/poetry.toml`, so phase 02's `poetry sync` and
# `redeploy_app.sh --install-dependencies` resolve to the same environment.
"$POETRY" config --local virtualenvs.in-project true
"$POETRY" env use python3.13
# `poetry sync`, not `poetry install --sync`: the flag is deprecated in
# Poetry 2.x. Sync rather than install so a dependency removed from the
# lockfile is removed from the environment too -- an install-only environment
# keeps stale packages that mask a missing declaration.
"$POETRY" sync
cd "$ROOT/frontend"
npm ci
