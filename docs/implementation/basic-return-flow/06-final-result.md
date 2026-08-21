# 06 · Final result

## 1 · Baseline and final state

| | |
|---|---|
| Branch | `refactor/unified-return-platform` |
| Baseline commit | `47f5abd7fad4e9f0e2c890ef7e762b37e45296e6` |
| Working tree at start | clean |
| Commits | `714239a`, `1b8bf20`, `eaed61c`, plus this receipt |

## 2 · Infrastructure and host services

Docker, infrastructure only: `mongodb` (+ `mongodb-rs-init`), `sqlserver`
(+ `sqlserver-init`), `neo4j`, `valkey`, `temporal`, `temporal-postgresql`,
`runtime-configuration-init`. All healthy through the backend's own readiness
probe, which uses the configured DSNs.

Host, directly on Windows: backend (uvicorn), frontend (vite), and five workers
-- `temporal`, `discovery`, `orchestrator`, `outbox`, `integration-outbox`.
Release adoption reached `LIVE` with `pending_process_classes: []`.

## 3 · Manual LLM configuration

`PLATFORM_AI_PROVIDER_ORDER=MANUAL`, `PLATFORM_AI_MANUAL_HANDOFF=AUTO`,
`interceptMode=false`. `GET /api/ai/routes` returns exactly two routes, both
`MANUAL / manual-human-v1`. Every reasoning request was held in the AI Control
Center and answered through `POST /api/ai/interceptions/{id}/answer`. **No live
provider was reachable**, which is a stronger statement than "none was called".

## 4 · Selected seeded order

`CQ800002` -- GARDEN account, INVOICED, one line, shipped quantity 1, warehouse
686, customer THELMA OSBORNE, product `6X12 CEIL ALUM 4-WAY REG SAND`, colour
`Sandtone`. Order numbers are unique across all 10,000 seeded documents.

## 5 · Defects found, and their root causes

Seventeen recorded in `02-findings-and-decisions.md`. The ones that mattered:

| | Defect | Root cause |
|---|---|---|
| F-4 | The Windows host stack could not stay up | `run_all_host.ps1` started a `jobs` worker `run_worker_host.ps1` does not accept; the launcher treats any child exit as fatal. It also never started `order-discovery-worker`, which is in `REQUIRED_PROCESS_CLASSES`, so adoption could never leave `ACTIVATING`. Linux had already fixed both. |
| F-5 | Bay assignment was structurally impossible | `platform.bay_configuration` held six bays for one warehouse; every seeded order names a different one. |
| F-7/8/9 | Manual mode could hold a turn and never resume one | The held request was keyed on a per-HTTP-request correlation id; a retried turn re-read the wall clock, so it asked a different question; and the answer, once found, was built and discarded. |
| F-11 | Every case reported `customer: null` | The only writer of the customer facts was a model that `redact_payload` forbids from seeing a customer. |
| F-12 | No colour anywhere | It is not on the order line and not in the schema's `product` entity. It is on the catalogue, unbound. |
| F-14 | Support was asked about a case id | The draft named one order reference and asked for an RMA. `businessPayload` was `{"caseId": ...}`. |
| F-15 | The handoff went before the return was described | Nothing sat between the policy gate and `_open_support`. |
| F-16 | A pasted order number found nothing | Identifier values were never trimmed, while the contract claimed they were normalised. |

## 6 · Files changed

**New:** `operations/support_handoff.py`,
`operations/order_lines/product_attributes.py`,
`operations/order_lines/case_detail.py`,
`workflows/case_customer_identity.py`,
`backend/scripts/seed_warehouse_bay_configuration.py`,
`tests/operations/test_support_handoff.py`,
`docs/implementation/basic-return-flow/`.

**Changed:** `ai/gateway/{final_dispatch,interception_policy,structured_invocation,telemetry}.py`,
`platform/reasoning/run_lifecycle.py`,
`dynamic_knowledge/integration/model_gateway.py`,
`dynamic_knowledge/order_agent/{coordinator,identification}.py`,
`workflows/{return_case_workflow,return_case_activities,return_case_launcher,worker}.py`,
`operations/return_support/service.py`, `operations/order_lines/__init__.py`,
`api/order_lines.py`, `configuration/return_configuration.py`,
`config/returns/production.yaml`, `scripts/run_all_host.ps1`,
`scripts/run_worker_host.ps1`,
`frontend/src/domains/support/SupportConsolePage.tsx`, four OpenAPI snapshots and
the generated TypeScript, and three test files.

## 7 · Tests

`pytest tests -q` -> **4040 passed, 3 skipped** (baseline 4025/3).
`vitest run` -> **471 passed** across 30 files.
`ruff` -> 1 known pre-existing error. `eslint` -> 5 known pre-existing errors.
OpenAPI drift check passes after regeneration.

## 8 · End-to-end result

Order number -> exactly one order -> confirm order and line -> capture quantity,
reason, condition and branch contact -> Workflow Agent invokes the Bay Assignment
Agent -> bay persisted -> one support work item -> the complete template rendered
in the Support Chat UI -> stopped before RMA.

Driven on case `10fcba5e-1312-404e-9dab-f7c5bdd25371`, work item
`8b2d9519-d033-4fc8-8688-e3c842d3398b`.

## 9 · Bay Assignment in Support Chat

Yes. `Assignment Status: RECOMMENDED`, `Recommended Bay: 686-BAY-01`,
`Warehouse/Branch: 686`, `Return Location: 686/686-BAY-01`,
`Bay Assignment Source: Bay Assignment Agent`, and the same values structured
under `businessPayload.bayAssignment`.

## 10 · What stayed excluded

No RMA, no shipping label, no tracking record, no Teams delivery, and **no
policy approval**. The case's `policyEvaluation` is `null` -- no route, no
decision -- and the message says `Policy Evaluation: Skipped by configuration`
with the operator's stated reason. The word `Approved` does not appear anywhere
in it. Teams code and documentation are untouched.

## 11 · Open items

Two adversarial cases were not closed and are recorded as open rather than as
passing: **12** (a material return detail changed *after* the handoff opened) and
**16** (return data changed after bay assignment, making it stale). Both need a
second live case to exercise honestly; asserting them from code that was not run
would be the kind of claim this ledger exists to prevent.
