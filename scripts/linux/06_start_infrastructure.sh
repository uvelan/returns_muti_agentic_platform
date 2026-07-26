#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT"
docker compose up -d --wait
docker compose ps --format json >"$EVIDENCE_DIR/infrastructure-services.json"
