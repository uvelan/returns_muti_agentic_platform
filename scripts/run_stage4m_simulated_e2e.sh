#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-BRANCH_PARCEL}"
API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
case "$SCENARIO" in
  BRANCH_PARCEL|OFFSITE_HEAVY|BRANCH_LTL|OFFSITE_PARCEL|DIRECT_VENDOR|NO_PHYSICAL_RETURN) ;;
  *) echo "Usage: $0 {BRANCH_PARCEL|OFFSITE_HEAVY|BRANCH_LTL|OFFSITE_PARCEL|DIRECT_VENDOR|NO_PHYSICAL_RETURN}" >&2; exit 2 ;;
esac
command -v jq >/dev/null || { echo "jq is required." >&2; exit 2; }
curl -fsS "$API/health/live" >/dev/null

if [[ "$SCENARIO" == "OFFSITE_HEAVY" || "$SCENARIO" == "BRANCH_LTL" ]]; then
  if [[ "$SCENARIO" == "BRANCH_LTL" ]]; then
    ORDER_REFERENCE="SO-SIM-E2E-BRANCH-LTL"
    RETURN_METHOD="BRANCH_LTL"
    PRODUCT_PRESENCE="PRESENT_AT_BRANCH"
  else
    ORDER_REFERENCE="SO-SIM-E2E-HEAVY"
    RETURN_METHOD="OFFSITE_LTL"
    PRODUCT_PRESENCE="OFFSITE_CUSTOMER_JOBSITE"
  fi
  CREATE_PAYLOAD="$(jq -n \
    --arg orderReference "$ORDER_REFERENCE" \
    --arg returnMethod "$RETURN_METHOD" \
    --arg productPresence "$PRODUCT_PRESENCE" '{
    customerReference:"CUSTOMER-SIM-E2E",
    orderReference:$orderReference,
    itemReferences:["LINE-SIM-HEAVY-1"],
    productReferences:["SKU-SIM-HEAVY-1"],
    productType:"BATHTUB",
    reasonCode:"DAMAGED",
    returnQuantity:1,
    packageCount:1,
    shippingPathExpectation:$returnMethod,
    orderSource:"SHOWROOM",
    trilogieOrderNumber:$orderReference,
    productPresence:$productPresence,
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
  case "$SCENARIO" in
    BRANCH_PARCEL)
      ORDER_REFERENCE="SO-SIM-E2E-PARCEL"
      RETURN_METHOD="PREPAID_PARCEL"
      PRODUCT_PRESENCE="PRESENT_AT_BRANCH"
      ;;
    OFFSITE_PARCEL)
      ORDER_REFERENCE="SO-SIM-E2E-OFFSITE-PARCEL"
      RETURN_METHOD="OFFSITE_PARCEL"
      PRODUCT_PRESENCE="OFFSITE_CUSTOMER_JOBSITE"
      ;;
    DIRECT_VENDOR)
      ORDER_REFERENCE="SO-SIM-E2E-DIRECT-VENDOR"
      RETURN_METHOD="DIRECT_VENDOR"
      PRODUCT_PRESENCE="OFFSITE_CUSTOMER_JOBSITE"
      ;;
    NO_PHYSICAL_RETURN)
      ORDER_REFERENCE="SO-SIM-E2E-NO-PHYSICAL"
      RETURN_METHOD="NO_PHYSICAL_RETURN"
      PRODUCT_PRESENCE="CUSTOMER_KEEP"
      ;;
  esac
  CREATE_PAYLOAD="$(jq -n \
    --arg orderReference "$ORDER_REFERENCE" \
    --arg returnMethod "$RETURN_METHOD" \
    --arg productPresence "$PRODUCT_PRESENCE" '{
    customerReference:"CUSTOMER-SIM-E2E",
    orderReference:$orderReference,
    itemReferences:["LINE-SIM-PARCEL-1"],
    productReferences:["SKU-SIM-PARCEL-1"],
    productType:"FAUCET",
    reasonCode:"DAMAGED",
    returnQuantity:1,
    packageCount:1,
    shippingPathExpectation:$returnMethod,
    orderSource:"FERGUSONHOME_WEB",
    sourceWebOrderNumber:"WSIM000001",
    trilogieOrderNumber:$orderReference,
    productPresence:$productPresence,
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
