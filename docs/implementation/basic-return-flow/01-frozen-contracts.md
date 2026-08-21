# 01 · Frozen contracts

Repository-verified only. Every row cites the file and symbol it was read from.
Nothing here is aspirational; a contract proven wrong is corrected here and
recorded as a finding in `02-findings-and-decisions.md`.

## C1 · Which conversational path is live

`frontend/src/domains/domainScreens.ts` maps `/returns` to
`domains/returns/ReturnCopilotPage` and `/support` to
`domains/support/SupportConsolePage`.

`ReturnCopilotPage.tsx` imports from `api/orderAgent`, `api/cases`,
`api/orderLines`, `api/runtimeConfig` and `api/returnHistory`. It does not import
`api/returnsDomain`. The live conversational path is therefore the **case**
world (`dynamic_knowledge/order_agent` plus `operations/case_repository`), driven
by the Temporal `ReturnCaseWorkflow`.

`operations/associate_flow.py` (`AssociateConversationService`) is a second,
older conversation path over `ReturnSession`. It is not what the Copilot page
calls. Verified by import list, not by assumption.

## C2 · Workflow Agent is `ReturnCaseWorkflow`

`backend/src/return_platform/workflows/return_case_workflow.py`,
`@workflow.defn(name="return-platform-return-case-v1")`, class
`ReturnCaseWorkflow`.

| Member | Line | Role |
|---|---|---|
| `_gather_bay` | 1046 | requests bay assignment, awaits the `bay_result` signal |
| `_evaluate_policy` | 1126 | the policy gate |
| `_policy_cleared` | 1106 | whether the gate is satisfied |
| `_open_support` | 1297 | opens the support work item |
| `bay_result` signal | 848 | receives `BayResultNotice` |
| `RequestBayAssignmentInput` | 565 | activity input |
| `OpenSupportWorkItemInput` | 660 | activity input |
| `DraftSupportRequestInput` | 654 | activity input |

## C3 · Bay Assignment Agent

`backend/src/return_platform/agents/bay_assignment.py::BayAssignmentAgent.assess`
takes `BayAssessmentRequest` and returns `BayAssessment`
(`agents/contracts/dto.py:173`).

The case-level caller is
`operations/warehouse/case_placement.py::CaseBayPlacement.recommend(case_id)`,
returning `CaseBayRecommendation` (`case_placement.py:168`) with
`warehouse_reference`, `bay_reference`, `return_location`,
`confidence_millionths`, `reason`, `explanation`, `evidence_reference`,
`graph_generation_id`, `eligible_bay_ids`, `capacity_evidence`.

Pre-receipt behaviour is already configured, not hardcoded:
`configuration.bay.allow_prearrival_reservation` decides whether a
recommendation may be produced before goods arrive; when false the result
carries `reason="PRE_ARRIVAL_NOT_ALLOWED"` and no bay. `BayAssignmentAgent.assess`
separately refuses with `PHYSICAL_RECEIPT_REQUIRED` when
`bay.require_physical_receipt` is set and the status is outside
`bay.eligible_statuses`.

**No RMA is required to produce a bay recommendation on the case path.**

## C4 · Support work item and Support Chat

`operations/return_support/service.py`:

- `CreateSupportWorkItemRequest` (121): `sessionId`, `subject`, `supportDraft`
  (10..16000 chars), `requestSnapshotDigest`, `priority`, `idempotencyKey`.
- `create_work_item` (301) writes the work item, message sequence 1 whose
  `messageText` is `request.supportDraft`, and a `businessPayload` containing
  only `{"requestSnapshotDigest": ...}`.
- `open_case_thread` (443) is the case-path equivalent.

`frontend/src/domains/support/SupportConsolePage.tsx::Message` (1472) renders
`{message.messageText}` in a bubble with no white-space handling.

## C5 · Manual LLM mode

Manual mode is the gateway's durable interception: a request is held as
`AIRequestStatus.INTERCEPTION_PENDING` and an operator answers it. Wiring:
`configuration/settings.py:228` `ai_interception_default`,
`ai/gateway/interception_policy.py`, `ai/interception/store.py`
(`AI_INTERCEPTIONS`), provider `MANUAL` in `config/ai_gateway.yaml:51`.

## C6 · Policy evaluation

Two distinct things share the word policy and must not be confused.

1. `operations/return_creation_policy.py::apply_active_return_policy` is request
   **validation** against `return_policy.normalized_return_methods`. Not a gate.
2. `ReturnCaseWorkflow._evaluate_policy`, with `PolicyGateState` (187),
   `PolicyRouteName` (209) and `PolicyDecisionName` (220), backed by AI task
   `RETURN_ELIGIBILITY_V1` (`config/ai_gateway.yaml:96`). This is the gate the
   directive disables through configuration.

## C7 · Configured return-detail fields

*(to verify -- Phase 5)*
