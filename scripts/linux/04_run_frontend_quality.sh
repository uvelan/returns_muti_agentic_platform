#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT/frontend"
npm run lint
npm run typecheck
npm run test -- --run
npm run build
