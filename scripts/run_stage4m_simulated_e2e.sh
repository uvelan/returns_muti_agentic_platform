#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-BRANCH_PARCEL}"
API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
case "$SCENARIO" in BRANCH_PARCEL|OFFSITE_HEAVY) ;; *) echo "Usage: $0 {BRANCH_PARCEL|OFFSITE_HEAVY}" >&2; exit 2 ;; esac
command -v jq >/dev/null || { echo "jq is required." >&2; exit 2; }
curl -fsS "$API/health/live" >/dev/null

if [[ "$SCENARIO" == "OFFSITE_HEAVY" ]]; then
  CREATE_PAYLOAD="$(jq -n '{
    customerReference:"CUSTOMER-SIM-E2E",
    orderReference:"SO-SIM-E2E-HEAVY",
    itemReferences:["LINE-SIM-HEAVY-1"],
    productReferences:["SKU-SIM-HEAVY-1"],
    productType:"BATHTUB",
    reasonCode:"DAMAGED",
    returnQuantity:1,
    packageCount:1,
    shippingPathExpectation:"OFFSITE_LTL",
    orderSource:"SHOWROOM",
    trilogieOrderNumber:"SO-SIM-E2E-HEAVY",
    productPresence:"OFFSITE_CUSTOMER_JOBSITE",
    branchReference:"BRANCH-SIM-001",
    associateReference:"ASSOCIATE-SIM-001",
    pickupAssessment:{
      pickupAddress:"100 Simulation Jobsite Road, Nashville, TN",
      onsiteContactName:"Simulation Customer",
      onsiteContactPhone:"555-0100",
      weight:386,
      weightUnit:"LB",
      length:78,
      width:44,
      height:42,
      dimensionUnit:"IN",
      palletized:true,
      loadingDockAvailable:false,
      forkliftAvailable:false,
      liftGateRequired:true,
      palletJackRequired:true,
      accessRestrictions:["APPOINTMENT_REQUIRED"]
    },
    channel:"SYSTEM",
    workflowMode:"PRODUCTION_V2"
  }')"
else
  CREATE_PAYLOAD="$(jq -n '{
    customerReference:"CUSTOMER-SIM-E2E",
    orderReference:"SO-SIM-E2E-PARCEL",
    itemReferences:["LINE-SIM-PARCEL-1"],
    productReferences:["SKU-SIM-PARCEL-1"],
    productType:"FAUCET",
    reasonCode:"DAMAGED",
    returnQuantity:1,
    packageCount:1,
    shippingPathExpectation:"PREPAID_PARCEL",
    orderSource:"FERGUSONHOME_WEB",
    sourceWebOrderNumber:"WSIM000001",
    trilogieOrderNumber:"SO-SIM-E2E-PARCEL",
    productPresence:"PRESENT_AT_BRANCH",
    branchReference:"BRANCH-SIM-001",
    associateReference:"ASSOCIATE-SIM-001",
    channel:"SYSTEM",
    workflowMode:"PRODUCTION_V2"
  }')"
fi

RETURN_JSON="$(curl -fsS -X POST "$API/api/v1/returns" -H 'Content-Type: application/json' -H "Idempotency-Key: stage4m-$SCENARIO-$(date +%s)" -d "$CREATE_PAYLOAD")"
SESSION_ID="$(jq -r '.data.id' <<<"$RETURN_JSON")"
[[ -n "$SESSION_ID" && "$SESSION_ID" != null ]] || { echo "$RETURN_JSON"; exit 1; }
echo "Created return session: $SESSION_ID"
HEADERS="$(mktemp)"; BODY="$(mktemp)"; trap 'rm -f "$HEADERS" "$BODY"' EXIT
curl -fsS -D "$HEADERS" -o "$BODY" -X POST "$API/api/v1/dependency-simulator/e2e/$SESSION_ID/run" -H 'Content-Type: application/json' -d "{\"scenario\":\"$SCENARIO\",\"useAiNarrative\":true,\"includeVendorRecovery\":true}"
grep -qi '^x-simulation-mode: true' "$HEADERS" || { echo "Missing X-Simulation-Mode header" >&2; cat "$HEADERS"; exit 1; }
jq . "$BODY"
STATE="$(curl -fsS "$API/api/v1/production-returns/$SESSION_ID/state")"
echo "Temporal workflow state:"
jq '.data | {sessionId,stage,returnCreated,physicalReturnComplete,receiptConfirmed,licensePlateAssigned,customerResolutionComplete,productDispositionComplete,vendorRecoveryComplete,caseFullyClosed}' <<<"$STATE"
CLOSED="$(jq -r '.data.caseFullyClosed' <<<"$STATE")"
[[ "$CLOSED" == "true" ]] || { echo "E2E workflow did not fully close." >&2; exit 1; }
SUMMARY="$(curl -fsS "$API/api/v1/dependency-simulator/summary")"
echo "Simulator AI metrics:"
jq '.data.ai | {requestCount,successCount,failureCount,fallbackCount,totalInputTokens,totalOutputTokens,totalTokens,estimatedCostMicrousd}' <<<"$SUMMARY"
