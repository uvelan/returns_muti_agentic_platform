#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT/frontend"
E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:5173}" \
  npm run test:e2e:real
cp test-results/real-e2e-results.json "$EVIDENCE_DIR/real-e2e-results.json"
