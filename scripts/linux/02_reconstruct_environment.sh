#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command python3
require_command node
require_command npm
[[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.13" ]] || {
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
if command -v poetry >/dev/null 2>&1; then
  poetry install --sync --no-interaction
else
  TOOLING_VENV="$RUNTIME_ROOT/tooling"
  python3 -m venv "$TOOLING_VENV"
  "$TOOLING_VENV/bin/pip" install --disable-pip-version-check "poetry==2.4.1"
  "$TOOLING_VENV/bin/poetry" install --sync --no-interaction
fi
cd "$REPO_ROOT/frontend"
npm ci --ignore-scripts=false
npx playwright install --with-deps chromium
