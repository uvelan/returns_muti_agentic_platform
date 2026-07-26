#!/usr/bin/env bash

run_scenario_with_evidence() {
  local scenario="$1"
  local evidence_prefix="$2"
  local api="$3"
  local log="$EVIDENCE_DIR/${evidence_prefix}.log"
  local state="$EVIDENCE_DIR/${evidence_prefix}-state.json"
  local session_id

  "$REPO_ROOT/scripts/run_stage4m_simulated_e2e.sh" "$scenario" | tee "$log"
  session_id="$(awk -F': ' '/^Created return session: / {print $2; exit}' "$log")"
  [[ -n "$session_id" ]] || {
    echo "Scenario $scenario did not emit a session ID." >&2
    return 1
  }

  curl --fail --silent --show-error \
    "$api/api/v1/production-returns/$session_id/state" --output "$state"
  jq -e '.data.caseFullyClosed == true' "$state" >/dev/null

  SCENARIO_SESSION_ID="$session_id"
  SCENARIO_STATE_FILE="$state"
  export SCENARIO_SESSION_ID SCENARIO_STATE_FILE
}
