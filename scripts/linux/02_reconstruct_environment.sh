#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command python3.13
require_command poetry
require_command node
require_command npm
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
poetry env use "$(command -v python3.13)"
poetry sync --no-interaction
backend_python_version="$(
  poetry run python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
)"
[[ "$backend_python_version" == "3.13" ]] || {
  echo "Backend Python 3.13 is required; found $backend_python_version." >&2
  exit 2
}
cd "$REPO_ROOT/frontend"
npm ci --ignore-scripts=false
npx playwright install --with-deps chromium
