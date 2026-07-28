#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT/backend"
if command -v poetry >/dev/null 2>&1; then
  poetry run python scripts/seed_e2e_data.py >"$EVIDENCE_DIR/seed-status.json"
elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
  "$RUNTIME_ROOT/tooling/bin/poetry" run python scripts/seed_e2e_data.py \
    >"$EVIDENCE_DIR/seed-status.json"
else
  echo "Poetry environment is unavailable; run phase 02 first." >&2
  exit 2
fi
python3 - "$EVIDENCE_DIR/seed-status.json" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text())
raise SystemExit(0 if data.get("ready") is True and not data.get("validationErrors") else 1)
PY
