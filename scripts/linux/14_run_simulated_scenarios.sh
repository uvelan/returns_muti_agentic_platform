#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/scenario_evidence.sh"

API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
declare -a scenarios=(
  BRANCH_PARCEL
  OFFSITE_HEAVY
  BRANCH_LTL
  OFFSITE_PARCEL
  DIRECT_VENDOR
  NO_PHYSICAL_RETURN
)

summary="$EVIDENCE_DIR/simulated-scenarios.jsonl"
: >"$summary"

for scenario in "${scenarios[@]}"; do
  run_scenario_with_evidence "$scenario" "simulated-${scenario}" "$API"
  jq -cn \
    --arg scenario "$scenario" \
    --arg sessionId "$SCENARIO_SESSION_ID" \
    --arg stateFile "$(basename "$SCENARIO_STATE_FILE")" \
    '{scenario:$scenario,sessionId:$sessionId,caseFullyClosed:true,stateFile:$stateFile}' \
    >>"$summary"
done

python3 - "$summary" "$EVIDENCE_DIR/simulated-scenarios-summary.json" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
items = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
expected = {
    "BRANCH_PARCEL",
    "OFFSITE_HEAVY",
    "BRANCH_LTL",
    "OFFSITE_PARCEL",
    "DIRECT_VENDOR",
    "NO_PHYSICAL_RETURN",
}
observed = {item["scenario"] for item in items}
if (
    len(items) != len(expected)
    or observed != expected
    or not all(item.get("caseFullyClosed") is True for item in items)
):
    raise SystemExit("Six-scenario closure evidence is incomplete.")
target.write_text(
    json.dumps({"status": "PASS", "scenarioCount": len(items), "items": items}, indent=2)
    + "\n",
    encoding="utf-8",
)
PY
