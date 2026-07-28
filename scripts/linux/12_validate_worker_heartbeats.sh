#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
output="$EVIDENCE_DIR/worker-heartbeats.json"
curl --fail --silent --show-error "$API/api/v1/system/dependencies" --output "$output"
python3 - "$output" <<'PY'
import json
import pathlib
import sys

cards = json.loads(pathlib.Path(sys.argv[1]).read_text()).get("data") or []
workers = [card for card in cards if card.get("category") == "WORKER"]
expected = {
    "return-workflow-worker",
    "return-orchestrator",
    "outbox-publisher",
    "data-job-worker",
}
healthy = {card.get("id") for card in workers if card.get("status") == "HEALTHY"}
missing = sorted(expected - healthy)
if missing:
    print("Missing or stale worker heartbeats: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY
