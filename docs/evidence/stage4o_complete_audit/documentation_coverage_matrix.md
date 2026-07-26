# Documentation Coverage Matrix

Documentation was checked as supporting evidence only.

| Feature | Implementation present? | Architecture documentation present? | Business documentation present? | Configuration documented? | API documented? | UI documented? | Run command present? | Validation command present? | Failure/recovery documented? | Evidence present? | Documentation accurate? | Classification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Platform Mongo ownership | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Source Mongo read-only boundary | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Neo4j derived projection | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| OMC authoritative business facts | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Temporal durable ownership | Yes | Yes | Yes | No | Yes | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Valkey transient-only use | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Platform SQL bay ownership | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| No AI-generated OMC SQL | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Generic write isolation from OMC | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| RMA versus RGA semantics | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Independent customer resolution | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Independent product resolution | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Vendor recovery non-blocking for customer completion | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Partial multi-anchor discovery | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | PARTIAL |
| Graph-first targeted fallback | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| FergusonHome W and web-to-Trilogie | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Candidate scoring and ambiguity | Yes | Yes | Yes | No | No | Yes | Yes | Yes | No | Yes | Yes | COMPLETE |
| Exact line confirmation and immutable snapshot | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Stale graph detection | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Typed item reason/condition/quantity | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | COMPLETE |
| Handling units and physical metadata | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Photo metadata and future OCR/image contract | Yes | Yes | Yes | Yes | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Branch/Associate contact and versioned Support snapshot | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Branch staging safety invariants | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Staging before branch handoff | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Internal queue and shared thread | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | PARTIAL |
| Return creation/readback/instructions/resolution | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Optional external mirroring | Yes | Yes | Yes | Yes | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Parcel milestone separation | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Parcel exception lifecycle | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Package-label identity | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Heavy pickup assessment | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Freight state separation | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| No-show and rescheduling | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| BRANCH_PARCEL path | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| OFFSITE_HEAVY path | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| BRANCH_LTL path | No | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| OFFSITE_PARCEL path | No | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| DIRECT_VENDOR path | No | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| NO_PHYSICAL_RETURN path | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| LSI simulator lifecycle | Yes | Yes | Yes | No | No | Yes | Yes | Yes | No | Yes | Yes | COMPLETE |
| Live LSI integration and reconciliation | No | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Receipt/inspection/discrepancy/license plate/disposition | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Advisory recommendation and atomic reservation | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Governed feedback recommendations | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Order Discovery Agent | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | COMPLETE |
| Return Workflow Agent | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | COMPLETE |
| Return Fulfillment Agent | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | Yes | PARTIAL |
| Bay Assignment Agent | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Feedback Learning Agent | Yes | Yes | Yes | No | Yes | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| OMC simulator | Yes | Yes | Yes | No | No | Yes | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Parcel simulator | Yes | Yes | Yes | No | No | Yes | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Freight simulator | Yes | Yes | Yes | No | No | Yes | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| LSI simulator | Yes | Yes | Yes | No | No | Yes | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| Simulator production isolation | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Provider/model/key lists and safe route IDs | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| Complexity task registry | Yes | Yes | Yes | No | No | Yes | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| Key/model/provider failover | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| Bounded retries and deadline | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | COMPLETE |
| Rate limiting | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Circuit breakers | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Prompt injection/domain/action firewall | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| Exact AI schemas and non-authority | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Durable attempt metrics | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | No | Yes | No | PARTIAL |
| Container configuration paths | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Concurrent Mongo index startup | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Worker heartbeat visibility | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
| OpenAPI/frontend alignment | Yes | Yes | Yes | No | No | No | Yes | Yes | Yes | Yes | Yes | PARTIAL |
| Accurate feature documentation | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | No | CONFLICTING |
| Static and unit quality gates | Yes | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | COMPLETE |
| Full business/browser E2E | No | Yes | Yes | No | No | No | Yes | Yes | No | Yes | Yes | PARTIAL |
