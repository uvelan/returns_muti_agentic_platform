# Stage 4L — Production Return Platform Implementation Report

**Implementation base:** `returns_muti_agentic_platform-master_2.zip`  
**Delivery classification:** `SOURCE_VALIDATED`  
**Delivery type:** production application foundation; not a standalone demo fork

## 1. Outcome

Stage 4L implements the Ferguson return process on top of the supplied repository while preserving the existing Data Console, AI Gateway, infrastructure, and operational foundations. The implementation introduces a production return domain with five bounded agents, a durable Temporal workflow contract, internal Returns Support collaboration, explicit external integration adapters, role-specific operational screens, configuration validation, and forward-only database changes.

The production path never uses AI to fabricate external success. AI may extract, rank, summarize, explain, and recommend. RMA creation, carrier booking, pickup, warehouse receipt, customer refund, and vendor recovery require authoritative system or human evidence.

## 2. Business semantics implemented

- v2 RMA/return number is customer and Associate facing.
- legacy v1 return remains a separate path.
- RGA is downstream vendor return authorization.
- customer resolution and product resolution are independent.
- a label is not dispatch.
- BOL tendering is not carrier booking.
- booking is not pickup.
- LSI license plates are linked after physical receipt.
- customer-facing completion does not wait for downstream vendor recovery.

## 3. Configuration-driven production model

Primary configuration:

```text
backend/config/returns/production.yaml
```

Validated loader:

```text
backend/src/return_platform/configuration/return_configuration.py
```

The loader validates:

- exactly the five required domain agents;
- source classification and scoring policy;
- smart-question definitions;
- return methods and product-presence values;
- damage-evidence requirements;
- heavy-pickup required fields;
- workflow stages and completion dimensions;
- OMC RMA/RGA semantics;
- branch-staging invariants;
- prohibition of AI-fabricated external outcomes.

The configuration SHA-256 is persisted in `return_configuration_snapshots` during application startup.

## 4. Five bounded agents

Implemented under:

```text
backend/src/return_platform/agents/
```

### Order Discovery Agent

- normalizes partial evidence;
- classifies likely source;
- scores candidates;
- explains matches and conflicts;
- recommends the next question;
- cannot confirm an ambiguous order.

### Return Workflow Agent

- identifies missing fields;
- enforces photo and pickup evidence policies;
- recommends a physical return path;
- generates a versioned Support draft;
- cannot submit or approve on behalf of a human.

### Return Fulfillment Agent

- normalizes OMC evidence;
- distinguishes RMA/RGA and customer/product resolution;
- distinguishes label, tender, booking, pickup, receipt, and license plate;
- cannot write raw OMC SQL or assert physical events.

### Bay Assignment Agent

- ranks eligible bays;
- explains exclusions;
- recommends hold or overflow;
- cannot bypass receipt/readiness or mutate capacity directly.

### Feedback Learning Agent

- measures rework, delays, mismatches, and policy outcomes;
- produces reviewed recommendations;
- cannot modify production configuration automatically.

Agent decisions are persisted in `agent_decisions` with evidence and configuration versions.

## 5. Associate and discovery implementation

Extended:

```text
backend/src/return_platform/operations/associate_flow.py
frontend/src/features/operations/AssociateReturnsPage.tsx
```

Implemented capabilities:

- graph-first candidate discovery with targeted source fallback;
- customer, phone, email, order, invoice, tracking, product, and location anchors;
- FergusonHome `W` order classification;
- preservation of source web and Trilogie references;
- source-channel classification;
- candidate scoring, evidence, conflict penalties, and ambiguity handling;
- explicit Associate confirmation lock;
- append-only Associate messages;
- immutable discovery and request snapshots;
- return item and handling-unit projections;
- product-presence and pickup assessment;
- damage artifact metadata requirements;
- creation of an internal Returns Support work item after confirmation.

OCR and image analysis are intentionally disabled. Artifact metadata and extension boundaries are present so those workers can be added later without changing return contracts.

## 6. Internal Returns Support collaboration

Implemented:

```text
backend/src/return_platform/operations/return_support/service.py
backend/src/return_platform/api/return_support.py
frontend/src/features/operations/ProductionReturnPages.tsx
```

The platform owns a durable business Support queue and thread. It supports:

- work-item creation;
- assignment;
- acknowledgment;
- multiple clarification messages;
- Associate replies;
- return-creation request;
- authoritative RMA/legacy-return readback;
- shipping instructions;
- customer-resolution recording;
- optional external ticket mirroring through the outbox.

Internal work-item creation and its initial message/outbox record are transactionally persisted. External ticket failure does not remove or corrupt the internal work item.

## 7. Production workflow v2

Implemented:

```text
backend/src/return_platform/workflows/production_return_state.py
backend/src/return_platform/workflows/production_return_workflow.py
backend/src/return_platform/operations/production_workflow.py
```

Temporal owns waits, timers, signals, and replay-safe lifecycle coordination. MongoDB owns business state and evidence. The workflow supports:

- branch parcel;
- branch LTL;
- offsite parcel;
- offsite heavy/LTL pickup;
- direct vendor;
- no-physical-return;
- receipt and license plate;
- customer resolution;
- product disposition;
- warehouse processing;
- downstream RGA/vendor credit.

Independent status dimensions are preserved rather than collapsed into one completion flag.

New sessions use `workflowMode=PRODUCTION_V2`. The legacy orchestrator only claims `LEGACY_V1` or pre-migration records, preventing the old automatic linear path from processing new production sessions.

## 8. Physical operations and artifacts

Implemented:

```text
backend/src/return_platform/operations/physical/service.py
backend/src/return_platform/api/physical_operations.py
backend/src/return_platform/api/return_artifacts.py
```

Controls include:

- manufacturer box must not be directly marked;
- return items must not enter branch inventory;
- return-number tag is required when branch staging applies;
- pickup-site assessment and required equipment fields;
- booking request separate from booking confirmation;
- booking separate from pickup confirmation;
- metadata-only artifacts with storage-provider references and hashes.

## 9. External dependencies and outbox

Implemented:

```text
backend/src/return_platform/operations/integrations/contracts.py
backend/src/return_platform/operations/integrations/outbox.py
backend/src/return_platform/workers/integration_outbox.py
backend/src/return_platform/api/integration_outbox.py
```

Adapter protocols exist for:

- OMC commands/readback;
- carrier booking/pickup;
- external ticket mirroring;
- customer notification;
- artifact storage.

The lease-based outbox provides correlation, idempotency, retry, and blocked-external-dependency behavior. Missing endpoints are reported as blocked; no fake ticket, RMA, carrier, or refund identifier is generated.

## 10. Warehouse placement

Implemented:

```text
backend/src/return_platform/operations/warehouse/service.py
backend/src/return_platform/api/warehouse_placement.py
backend/src/return_platform/operations/sql_business_state.py
```

The Bay Assignment Agent recommends eligible bays. A deterministic SQL service performs atomic reservation and assignment after physical readiness. Platform bay data remains governed by the configured warehouse-location authority.

Forward-only SQL migrations:

```text
infra/sqlserver/init/003_production_return_platform.sql
infra/sqlserver/init/004_production_bay_constraints.sql
```

They add only platform/sandbox compatibility structures. They do not alter the supplied production OMC tables.

## 11. Persistence and schema registry

Production collections include:

```text
associate_messages
discovery_snapshots
return_request_snapshots
operational_return_items
handling_units
pickup_sites
pickup_requests
branch_staging_records
document_artifacts
support_work_items
support_messages
shipping_instructions
shipment_events
omc_command_records
agent_decisions
vendor_return_links
integration_outbox
return_configuration_snapshots
schema_migrations
```

The repository now models 62 physical assets in `backend/config/schema_registry.yaml`, and the AI Studio generator coverage validator confirms every declared generator is implemented.

## 12. Production screens

Frontend routes include:

```text
/associate/returns
/operations/returns/:sessionId
/operations/return-agents
/return-support/workbench
/logistics/returns
/warehouse/returns
/tracking/returns
/system/integration-outbox
/system/dependencies
```

The return detail view exposes:

- order/customer evidence;
- return items and handling units;
- pickup assessment;
- branch staging;
- artifacts;
- Support conversation;
- shipping and shipment events;
- OMC command/readback evidence;
- integration outbox status;
- agent decisions;
- license plates and vendor-return links;
- complete audit timeline.

## 13. API registration

New production routers:

```text
return_agents
return_support
production_workflow
physical_operations
return_artifacts
warehouse_placement
integration_outbox
```

All are registered in `backend/src/return_platform/main.py`.

## 14. Validation completed

Passed in the packaging environment:

```text
python -m compileall -q backend/src backend/tests
21 focused pytest tests
python scripts/validate_stage4l_production.py
python scripts/validate_stage4_source.py
python scripts/validate_stage4_contracts.py
node scripts/validate_frontend_syntax.mjs
```

The Stage 4L validator proves 12 contract groups including agent registration, lifecycle state separation, schema coverage, route registration, role separation, external-integration boundaries, legacy-orchestrator isolation, and forward-only SQL migration policy.

Evidence is stored under:

```text
docs/evidence/stage4l_production/
```

## 15. Validation limitations

This environment lacks the complete runtime and required release toolchain. The following were not run:

- Ruff;
- strict mypy;
- full dependency-backed pytest;
- OpenAPI export and TypeScript regeneration;
- frontend ESLint, project typecheck, Vite build, Vitest, Playwright, and accessibility tests;
- live infrastructure and integration tests.

The current classification is therefore `SOURCE_VALIDATED`, not `PRODUCTION_VALIDATED`.
