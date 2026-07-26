# Stage 4M — Production-Safe External Dependency Simulation Plan

## Objective

Keep the production return architecture intact while providing live-service emulators for the external systems that are currently out of scope:

- OMC SQL Server commands and readback;
- parcel label and tracking service;
- Freight/TMS quote, BOL, tender, booking and pickup service;
- LSI receipt, license plate, disposition, RGA and vendor-credit exchanges.

The simulators exist only in development and test. They implement the same gateway boundaries that later real adapters will implement. Startup rejects simulated dependency modes in production.

## Delivery sequence

1. Add a validated YAML registry for operations, states, lightweight models, fallback policy and configurable AI pricing.
2. Add deterministic OMC, Parcel, Freight/TMS and LSI state machines.
3. Add idempotent MongoDB operation and AI-metric persistence.
4. Add optional lightweight AI narrative enrichment using Google Flash-Lite and NVIDIA 3B-class models.
5. Guarantee default-template responses after timeout, authentication failure, provider failure or invalid JSON.
6. Connect confirmed simulator operations to the existing production Temporal workflow.
7. Connect the transactional integration outbox to simulator dispatchers when a dependency mode is `SIMULATED`.
8. Add dedicated overview, OMC, Parcel, Freight, LSI, AI Metrics and operation-detail pages.
9. Add run scripts, a real API-driven E2E script, focused tests and source validation.
10. Update README and environment examples.

## Non-negotiable rules

- Deterministic code owns identifiers, state transitions, idempotency, success/failure and workflow events.
- AI changes only `message`, `summary` and `nextAction` wording.
- AI failure never changes the external-operation result and never blocks the return flow.
- RMA and RGA remain different objects.
- RGA creation requires RTV product resolution.
- Label creation is not carrier acceptance.
- Freight tender is not booking; booking is not pickup.
- Every AI attempt and fallback is persisted with provider, model, tokens, latency, status, error and configured cost estimate.
- All simulated identifiers contain `SIM` and all responses include the `X-Simulation-Mode: true` header.
- Simulation is rejected when `PLATFORM_ENVIRONMENT=production`.
