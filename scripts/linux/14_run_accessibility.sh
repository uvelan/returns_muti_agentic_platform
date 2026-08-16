#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# THIS PHASE HAS NO SUITE TO RUN, AND SAYS SO INSTEAD OF FAILING OPAQUELY.
#
# Wave F4 (`6a1a0aa`) deleted `frontend/tests/a11y.spec.ts` along with the
# 76-route legacy shell it drove, and removed the `test:a11y` script from
# `frontend/package.json` in the same commit. Nothing replaced either. This
# script kept calling `npm run test:a11y`, so it failed with
# `npm ERR! Missing script: "test:a11y"` -- an error that reads as a broken npm
# install rather than as a deleted test suite, which is the whole reason it is
# spelled out here.
#
# The script is kept, not deleted: the gate is still wanted, the work is writing
# the replacement spec for the nine canonical domains. Restoring it means adding
# an a11y spec under `frontend/tests/` and a `test:a11y` script, then
# uncommenting this phase in `validation_phases.txt`.
cd "$REPO_ROOT/frontend"

if ! node -e 'process.exit(require("./package.json").scripts["test:a11y"] ? 0 : 1)'; then
  cat >&2 <<'EOF'
Accessibility phase cannot run: frontend/package.json has no "test:a11y" script.

Wave F4 deleted frontend/tests/a11y.spec.ts and the script that ran it, and no
replacement was written for the nine canonical domains. This is a missing gate,
not a configuration error.

To restore it:
  1. add an accessibility spec under frontend/tests/
  2. add "test:a11y": "playwright test tests/a11y.spec.ts" to frontend/package.json
  3. uncomment 14_run_accessibility.sh in scripts/linux/validation_phases.txt
EOF
  exit 2
fi

result="$EVIDENCE_DIR/accessibility-results.json"
E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:5173}" \
PLAYWRIGHT_JSON_OUTPUT_FILE="$result" \
  npm run test:a11y -- --reporter=json

[[ -s "$result" ]] || {
  echo "Accessibility JSON result was not generated." >&2
  exit 1
}
jq -e '.stats.unexpected == 0' "$result" >/dev/null
