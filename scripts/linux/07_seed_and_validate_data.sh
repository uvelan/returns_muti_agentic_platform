#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# Seeding resolves every datastore credential through Vault, so a sealed Vault
# fails here with a connection error naming `vault-resolved.invalid` -- the
# `.env` sentinel, not a host. Say which it is before spending the timeout.
assert_vault_unsealed

cd "$REPO_ROOT/backend"
backend_python
"${BACKEND_PYTHON[@]}" scripts/seed_e2e_data.py >"$EVIDENCE_DIR/seed-status.json"
python3 - "$EVIDENCE_DIR/seed-status.json" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text())
raise SystemExit(0 if data.get("ready") is True and not data.get("validationErrors") else 1)
PY
