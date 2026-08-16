#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command python3.13
require_command node
require_command npm
# Not `require_command poetry`. `scripts/bootstrap_host.sh` installs Poetry into
# `.tmp/poetry` and deliberately does not add it to PATH, so demanding it on
# PATH failed this phase on hosts prepared exactly as documented. `poetry_cmd`
# finds it wherever it actually is, and still fails with a precise message when
# it is genuinely absent.
poetry_cmd
[[ "$(python3.13 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.13" ]] || {
  echo "Python 3.13 is required." >&2
  exit 2
}
[[ "$(node -p 'process.versions.node.split(".")[0]')" == "24" ]] || {
  echo "Node.js 24 is required; found $(node --version)." >&2
  exit 2
}
[[ "$(npm --version | cut -d. -f1)" == "11" ]] || {
  echo "npm 11 is required; found $(npm --version)." >&2
  exit 2
}
cd "$REPO_ROOT/backend"
# Keep the environment at `backend/.venv`, which is where every no-Poetry
# fallback in this repository looks. Poetry's default is a cache directory keyed
# by a hash of the project path, and nothing here can find that.
"${POETRY_CMD[@]}" config --local virtualenvs.in-project true
"${POETRY_CMD[@]}" env use "$(command -v python3.13)"
"${POETRY_CMD[@]}" sync --no-interaction
backend_python_version="$(
  "${POETRY_CMD[@]}" run python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
)"
[[ "$backend_python_version" == "3.13" ]] || {
  echo "Backend Python 3.13 is required; found $backend_python_version." >&2
  exit 2
}
cd "$REPO_ROOT/frontend"
npm ci --ignore-scripts=false
# `--with-deps` runs `apt-get install` for Chromium's shared libraries and needs
# root. On a clean unprivileged host it fails, and it fails AFTER npm ci has
# already succeeded, so the phase looks like a dependency problem rather than a
# permissions one. Try it, and fall back to the browser-only install with the
# missing-libraries hint spelled out -- a browser without its system libraries
# is a diagnosable state; an aborted phase 02 is not.
if ! npx playwright install --with-deps chromium; then
  echo "playwright install --with-deps failed (it needs root to apt-get system libraries)." >&2
  echo "Retrying without --with-deps; if the browser will not launch later, run as root:" >&2
  echo "  npx playwright install-deps chromium" >&2
  npx playwright install chromium
fi
