# Stage 4L — Production Return Platform Implementation Plan

**Implementation base:** `returns_muti_agentic_platform-master_2.zip`  
**Delivery mode:** production foundation, not a standalone demo application  
**Deferred capabilities:** OCR, computer vision, image classification, document extraction, and advanced dynamic-agent composition

## 1. Objectives

Build the Ferguson return application on the existing repository with:

- five bounded domain agents;
- Temporal-owned durable workflow coordination;
- Platform MongoDB-owned business workflow state;
- configuration-driven mappings, questions, SLAs, routing, and thresholds;
- graph-first order discovery with targeted source fallback;
- internal Returns Support collaboration that works without an external ticket system;
- explicit OMC, carrier, notification, and external ticket adapters;
- Associate, Returns Support, Logistics, Warehouse, Tracking, Agent Evidence, and Dependency screens;
- no runtime mocks in production paths;
- extension points for later OCR and image processing.

## 2. Non-negotiable ownership

| Concern | Owner |
|---|---|
| Conversations, workflow projections, work items, pickup assessment, audit, outbox, idempotency | Platform MongoDB |
| Return/RMA, cart/cart item, customer/product resolution, return method, freight facts | OMC through approved gateway |
| Discovery graph | Neo4j, derived and rebuildable |
| Durable waits, timers, signals | Temporal |
| Live fan-out/cache | Valkey |
| Bay configuration/reservation/assignment | Platform SQL unless LSI authority replaces it |
| Human business approval | Associate, Returns Support, Logistics, Warehouse roles |

## 3. AI responsibilities

### Order Discovery Agent

- normalize partial evidence;
- classify likely source channel;
- rank candidate orders and lines;
- explain evidence and conflicts;
- recommend the next question;
- never confirm an ambiguous order.

### Return Workflow Agent

- identify missing fields;
- enforce evidence requirements;
- recommend physical path;
- create a versioned Support request draft;
- summarize clarification messages;
- never submit or approve on behalf of a person.

### Return Fulfillment Agent

- normalize OMC statuses;
- distinguish RMA from RGA;
- distinguish label/tender/booking/pickup/receipt;
- detect stale, conflicting, or missing evidence;
- never write raw OMC SQL or assert physical events.

### Bay Assignment Agent

- rank eligible bays;
- explain exclusions;
- recommend overflow/hold;
- never bypass receipt/readiness or atomically mutate capacity itself.

### Feedback Learning Agent

- calculate operational metrics;
- identify recurring rework and assumption mismatches;
- produce human-reviewed recommendations;
- never auto-change production policy.

## 4. Configuration model

Create validated YAML configuration for:

- agent versions and enabled capabilities;
- discovery anchors and scoring weights;
- smart-question fields and priorities;
- return-method mappings;
- photo/evidence requirements;
- heavy-pickup required fields;
- workflow stage order and SLAs;
- assumption-set version;
- Support channel and external-adapter modes;
- OMC normalized status mappings;
- bay eligibility rules.

Application startup must fail on invalid configuration.

## 5. Production services

Implement:

1. `ReturnConfigurationLoader`
2. `ReturnAgentRegistry`
3. `OrderDiscoveryAgent`
4. `ReturnWorkflowAgent`
5. `ReturnFulfillmentAgent`
6. `BayAssignmentAgent`
7. `FeedbackLearningAgent`
8. `ReturnSupportService`
9. `SupportWorkItemRepository`
10. `ReturnIntegrationOutbox`
11. Adapter protocols for OMC, carrier, ticketing, notification, and document storage
12. Production workflow v2 state and signals

## 6. Persistence

Add Platform MongoDB collections and indexes:

- `support_work_items`
- `support_messages`
- `associate_messages`
- `discovery_snapshots`
- `return_request_snapshots`
- `operational_return_items`
- `handling_units`
- `pickup_sites`
- `pickup_requests`
- `shipping_instructions`
- `shipment_events`
- `omc_command_records`
- `agent_decisions`
- `integration_outbox`
- `vendor_return_links`
- `document_artifacts`
- `return_configuration_snapshots`
- `schema_migrations`

Existing `support_cases` remains an operational exception queue, not the business Support queue.

## 7. APIs

Add:

- configuration and agent-capability inspection;
- Support work-item queue, assignment, acknowledgement, messages, clarification, and completion;
- return-agent assessment endpoint;
- logistics pickup queue and actions;
- warehouse inbound/receipt/inspection endpoints;
- tracking aggregate endpoint;
- agent decision/evidence endpoint.

Every mutation requires actor, role, correlation ID, optimistic version, idempotency key where externally retried, and audit event.

## 8. External dependencies

### Returns Support or ticketing

The platform’s internal Support work item is authoritative for the workflow. External ticket systems are mirrors/adapters.

- AI drafts the request.
- A human confirms submission.
- The platform creates the internal work item transactionally.
- The outbox requests external ticket creation.
- The external reference is attached when received.
- External failure does not erase the internal work item.

### OMC

Use an approved command gateway. No arbitrary table writes.

### Carrier

Use an adapter with explicit states: requested, tendered, booked, arrived, picked up, received.

### OCR/images

Provide document-artifact interfaces now. Add OCR and image-processing workers later without changing return contracts.

## 9. Screens

Build or extend:

1. Associate Return Intake
2. Candidate Comparison and Confirmation
3. Return Details and Evidence
4. Product Presence and Handling Units
5. Pickup Assessment
6. Support Draft and Conversation
7. Returns Support Queue and Work Item
8. Logistics Pickup Queue and Detail
9. Warehouse Inbound, Receipt, Inspection, and Bay Recommendation
10. Return Tracking
11. Agent Decisions and Evidence
12. Dependencies and Integration Outbox
13. Customer-safe Return Timeline

## 10. Validation gates

- Ruff
- strict mypy
- pytest
- OpenAPI export/generation consistency
- frontend lint
- frontend typecheck/build
- Vitest
- Playwright smoke paths
- no-runtime-mocks scan
- configuration validation
- API route/source parity

## 11. Stage 4L delivery boundary

This stage delivers the production architecture, configuration, domain agents, internal Support workflow, integration contracts/outbox, workflow v2 contract, core role APIs, and corresponding production screens.

It does not claim live OMC, carrier, or external ticket production validation until credentials, procedures, and sandbox endpoints are provided.


## 12. Implementation status

Stage 4L source implementation is complete in the delivery repository. The following have been implemented:

- validated production return configuration and startup snapshot;
- all five bounded agents and registry;
- graph-first Associate discovery extensions;
- internal Returns Support work queue and shared message thread;
- production Temporal workflow v2 and deterministic lifecycle state;
- branch staging, pickup, freight, receipt, license-plate, resolution, and vendor-recovery projections;
- integration adapter contracts and lease-based transactional outbox;
- warehouse bay recommendation plus atomic reservation/assignment service;
- production Associate, Support, Logistics, Warehouse, Tracking, Agent Evidence, and Integration Outbox screens;
- forward-only Platform SQL migrations;
- source validation, focused unit tests, and evidence artifacts.

Current evidence classification: `SOURCE_VALIDATED`. Live infrastructure and dependency-backed release gates remain in the future-production plan.
