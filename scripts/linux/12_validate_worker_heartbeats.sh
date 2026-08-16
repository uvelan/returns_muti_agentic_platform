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
# These three, and only these three, are the WORKER cards
# `api/dependencies.py` emits. `order-discovery-worker` and
# `integration-outbox-worker` are equally required but publish no card, which is
# why the adoption check below exists rather than a longer list here.
expected = {
    "return-workflow-worker",
    "return-orchestrator",
    "outbox-publisher",
}
healthy = {card.get("id") for card in workers if card.get("status") == "HEALTHY"}
missing = sorted(expected - healthy)
if missing:
    print("Missing or stale worker heartbeats: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY

# The heartbeat check above cannot see two of the five required process classes,
# so on its own it reports a healthy platform while `order-discovery-worker` or
# `integration-outbox-worker` is absent -- exactly the failure
# `09_start_workers.sh` records in its own comment, where a missing discovery
# worker left adoption stuck ACTIVATING with no error anywhere. Adoption is the
# check that covers all five, because a class that never started can never
# report the activated release.
#
# ACTIVATED IS NOT LIVE. `/health/ready` will answer 200 throughout.
adoption="$EVIDENCE_DIR/config-adoption.json"
curl --fail --silent --show-error "$API/api/config/adoption" --output "$adoption"
python3 - "$adoption" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
data = payload.get("data", payload)
status = data.get("status")
if status == "LIVE":
    raise SystemExit(0)

print(f"Configuration adoption is {status}, not LIVE.", file=sys.stderr)

if status == "NO_ACTIVE_RELEASE":
    print(
        "No configuration release is activated, so nothing can adopt one. "
        "Run scripts/prepare_runtime_configuration.sh, which publishes the "
        "initial release when none is active.",
        file=sys.stderr,
    )
    raise SystemExit(1)

# `ReleaseAdoptionState.pending_process_classes` -- serialized with the field
# name, since the model declares no alias generator. Naming the classes is the
# entire point: "not live" without saying what it is waiting for is not
# actionable.
pending = data.get("pending_process_classes") or []
if pending:
    print(
        "Process classes that have not adopted: " + ", ".join(sorted(pending)),
        file=sys.stderr,
    )
print(
    "Every required class must be running AND reporting the activated release id "
    "and head revision. Start the full set with scripts/linux/09_start_workers.sh.",
    file=sys.stderr,
)
raise SystemExit(1)
PY
