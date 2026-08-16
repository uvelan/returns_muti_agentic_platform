#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# THIS PHASE HAS NO SUITE TO RUN, AND SAYS SO INSTEAD OF FAILING OPAQUELY.
#
# Wave F4 (`6a1a0aa`) deleted `frontend/playwright.real.config.ts` and the
# `test:e2e:real` script that used it. The surviving Playwright suite is
# `frontend/tests/canonical-domains.spec.ts`, run by `npm run test:e2e` -- but
# that config's `webServer` starts the MSW mock server on 5174 and defaults its
# baseURL there, so it exercises the mocked frontend, not the live stack. It is
# not a drop-in substitute for a real-stack run and is deliberately not
# substituted here: a green "real E2E" that never touched the backend is worse
# than an absent one.
#
# The script is kept, not deleted: the gate is still wanted. Restoring it means
# a Playwright config that points at the running host frontend with no
# `webServer` and no mock mode, plus a `test:e2e:real` script, then uncommenting
# this phase in `validation_phases.txt`.
cd "$REPO_ROOT/frontend"

if ! node -e 'process.exit(require("./package.json").scripts["test:e2e:real"] ? 0 : 1)'; then
  cat >&2 <<'EOF'
Real end-to-end phase cannot run: frontend/package.json has no "test:e2e:real"
script, and frontend/playwright.real.config.ts no longer exists.

Wave F4 deleted both. `npm run test:e2e` is NOT a substitute: its config starts
the MSW mock server and points at it, so it never reaches the backend.

To restore it:
  1. add frontend/playwright.real.config.ts with no `webServer` and
     baseURL from E2E_BASE_URL
  2. add "test:e2e:real": "playwright test --config=playwright.real.config.ts"
  3. uncomment 14_run_real_e2e.sh in scripts/linux/validation_phases.txt
EOF
  exit 2
fi

E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:5173}" \
  npm run test:e2e:real
[[ -s test-results/real-e2e-results.json ]] || {
  echo "Playwright JSON result was not generated." >&2
  exit 1
}
cp test-results/real-e2e-results.json "$EVIDENCE_DIR/real-e2e-results.json"
