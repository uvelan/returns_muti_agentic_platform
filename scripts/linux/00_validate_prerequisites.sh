#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

for command in bash git python3 python3.13 poetry node npm docker curl jq tar sha256sum; do
  require_command "$command"
done

python_version="$(python3.13 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$python_version" == "3.13" ]] || {
  echo "Python 3.13 is required; found $python_version." >&2
  exit 2
}

node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$node_major" == "24" ]] || {
  echo "Node.js 24 is required; found $(node --version)." >&2
  exit 2
}

npm_major="$(npm --version | cut -d. -f1)"
[[ "$npm_major" == "11" ]] || {
  echo "npm 11 is required; found $(npm --version)." >&2
  exit 2
}

docker compose version >/dev/null
docker info >/dev/null

python3.13 "$LINUX_SCRIPT_DIR/validate_env.py" "$REPO_ROOT/.env" --simulation
printf 'Linux prerequisites, Docker daemon, and safe simulation configuration validated.\n'