# Workflow State Matrix

The production-v2 workflow is a durable event state machine; only two simulator scenarios are exposed.

| Scenario | Classification | Starting state | Required inputs | Workflow stages | Signals | Activities | Authoritative evidence | Full-closure condition | Gap |
|---|---|---|---|---|---|---|---|---|---|
| BRANCH_PARCEL | SIMULATED | INTAKE | confirmed discovery, return details, Support/RMA, branch handling/staging | INTAKE→SUPPORT→RETURN_CREATION→PHYSICAL_RETURN_SETUP→RETURN_SHIPMENT→RECEIPT→CUSTOMER_RESOLUTION→PRODUCT_DISPOSITION→WAREHOUSE_PROCESSING→VENDOR_RECOVERY→FULLY_CLOSED | record_production_event updates from simulator bridge | None in production v2 | Simulated identifiers and Temporal event state | all applicable dimensions terminal | No live-stack E2E result. |
| OFFSITE_HEAVY | PARTIAL | INTAKE | confirmed discovery, heavy pickup assessment | Generic production stages | OMC/FREIGHT/LSI simulator bridge | None in production v2 | Simulated only | generic all-dimensions predicate | Live stack blocked; readiness fields incomplete. |
| BRANCH_LTL | MISSING | Not dedicated | Not fully specified | Generic event state only | No dedicated scenario signal matrix | None | None | Generic predicate only | No dedicated orchestration and E2E. |
| OFFSITE_PARCEL | MISSING | Not dedicated | Not fully specified | Generic event state only | No dedicated scenario signal matrix | None | None | Generic predicate only | No dedicated orchestration and E2E. |
| DIRECT_VENDOR | MISSING | Not dedicated | Not fully specified | Generic event state only | No dedicated scenario signal matrix | None | None | Generic predicate only | No dedicated orchestration and E2E. |
| NO_PHYSICAL_RETURN | PARTIAL | Not dedicated | Not fully specified | Generic event state only | No dedicated scenario signal matrix | None | None | Generic predicate only | No dedicated orchestration and E2E. |
