#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

cd "$REPO_ROOT/frontend"
result="$EVIDENCE_DIR/accessibility-results.json"
E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:5173}" \
PLAYWRIGHT_JSON_OUTPUT_FILE="$result" \
  npm run test:a11y -- --reporter=json

[[ -s "$result" ]] || {
  echo "Accessibility JSON result was not generated." >&2
  exit 1
}
jq -e '.stats.unexpected == 0' "$result" >/dev/null
