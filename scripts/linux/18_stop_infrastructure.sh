#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
[[ "${1:-}" == "--stop" ]] || {
  echo "Usage: $0 --stop" >&2
  exit 2
}
docker compose -f "$REPO_ROOT/compose.yaml" down --remove-orphans
