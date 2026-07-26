"""Generate the pre-remediation Stage 4O audit baseline gathered on 2026-07-26.

This file is evidence provenance, not production application code. It must not
be used to overwrite the post-remediation validation summary.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DISPLAY_ROOT = r"K:\Projects\FEG\Ret\full\returns_platform"
COMMIT = "c8976dab36eee87c238da5a174bfd4800bc212cc"

VERIFIED = "VERIFIED_IMPLEMENTED"
SOURCE = "SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED"
PARTIAL = "PARTIAL"
SIMULATED = "SIMULATED"
MOCKED = "MOCKED"
CONFIG = "CONFIG_ONLY"
DOC = "DOCUMENTATION_ONLY"
MISSING = "MISSING"
UNSAFE = "UNSAFE"
NA = "NOT_APPLICABLE"


def feature(
    feature_id: str,
    domain: str,
    name: str,
    required: str,
    status: str,
    source: str,
    symbol: str,
    *,
    route: str = "None",
    persistence: str = "None",
    test: str = "None found",
    screen: str = "None",
    workflow: str = "None",
    configuration: str = "None",
    documentation: str = "README.md and Stage 4 documents",
    security: str = "Role middleware and typed contracts where routed",
    gap: str = "None identified",
    risk: str = "LOW",
    action: str = "Retain and regression-test.",
    authoritative: str = "Platform",
) -> dict[str, Any]:
    return {
        "Feature ID": feature_id,
        "Domain": domain,
        "Feature": name,
        "Feature name": name,
        "Required behavior": required,
        "Implementation classification": status,
        "Classification": status,
        "Business purpose": required,
        "Source evidence": source,
        "Source file path": source,
        "Class/function/component/config symbol": symbol,
        "API route, event, signal, or command involved": route,
        "API": route,
        "Persistence used": persistence,
        "Persistence": persistence,
        "Authoritative system": authoritative,
        "Test file and test name": test,
        "Test": test,
        "Runtime evidence file, when available": (
            "test_and_runtime_matrix.json" if status in {VERIFIED, SIMULATED} else "None"
        ),
        "Runtime evidence": "See test_and_runtime_matrix.json",
        "Documentation file and section": documentation,
        "Documentation": documentation,
        "Screen": screen,
        "Agent": name if "Agent" in name else "None",
        "Workflow stage": workflow,
        "Configuration": configuration,
        "Security control": security,
        "Missing pieces": gap,
        "Known gap": gap,
        "Risk": risk,
        "Priority": risk,
        "Recommended next action": action,
        "Recommended action": action,
    }


features: list[dict[str, Any]] = [
    feature("OWN-01", "Infrastructure", "Platform Mongo ownership", "Platform Mongo owns operational sessions, messages, snapshots, handling, events, audit, idempotency, outbox, simulation, and AI metrics.", PARTIAL, "backend/src/return_platform/operations/repository.py; backend/src/return_platform/workflows/persistence.py", "OperationalRepository; MongoReturnSessionRepository", persistence="Multiple platform Mongo collections", gap="Two session/event/outbox implementations coexist; no dedicated idempotency collection.", risk="HIGH", action="Consolidate the legacy operational_returns model and production return_sessions model."),
    feature("OWN-02", "Security", "Source Mongo read-only boundary", "Source Mongo is read-only in production and cannot be mutated by business paths.", SOURCE, "backend/src/return_platform/operations/repository.py", "source_order; apply_seed; reset_demo_data", persistence="Source MongoDB", test="backend/tests/test_stage4_schema_and_seed_contracts.py", security="Writes are environment-gated to development/test.", gap="The same repository and credentials expose write methods; database-level read-only credentials were not proven.", risk="HIGH", action="Use a separately permissioned read-only Source Mongo client outside isolated seed tooling.", authoritative="Source MongoDB"),
    feature("OWN-03", "Infrastructure", "Neo4j derived projection", "Neo4j is graph-first discovery evidence but rebuildable and not transaction authority.", SOURCE, "backend/src/return_platform/data_platform/graph/sync_service.py; backend/src/return_platform/operations/associate_flow.py", "GraphSyncService; _targeted_graph_upsert", persistence="Neo4j plus graph_sync_runs", test="backend/tests/test_customer_graph_projection_materializer.py", authoritative="Source MongoDB"),
    feature("OWN-04", "RMA/OMC", "OMC authoritative business facts", "OMC owns Return/RMA, cart/item, method, reason/fault, resolutions, freight, license plate, RGA, and vendor credit.", PARTIAL, "backend/src/return_platform/operations/integrations/outbox.py; backend/src/return_platform/operations/sql_business_state.py", "OutboxIntegrationService; SQLBusinessStateRepository", route="omc.return.create outbox topic", persistence="Platform outbox and local SQL replica", gap="No live OMC gateway/readback implementation proves the full fact set; local SQL duplicates return facts.", risk="BLOCKER", action="Implement and contract-test the real OMC gateway and authoritative readback.", authoritative="OMC SQL Server"),
    feature("OWN-05", "Infrastructure", "Temporal durable ownership", "Temporal owns execution, waits, signals, retries, timers, and SLAs.", PARTIAL, "backend/src/return_platform/workflows/production_return_workflow.py", "ProductionReturnWorkflow", route="record_production_event update; production_state query", persistence="Temporal history", test="backend/tests/test_production_return_state.py", gap="Durable wait and idempotent events exist, but production v2 has no activities, retry policies, timers, SLA timers, cancellation handler, or out-of-order buffering.", risk="HIGH", action="Add scenario-specific durable timers, activities, retry policies, cancellation, and reconciliation."),
    feature("OWN-06", "Infrastructure", "Valkey transient-only use", "Valkey is limited to transient events, caching, coordination, and rate delivery.", SOURCE, "backend/src/return_platform/operations/events.py; backend/src/return_platform/workers/integration_outbox.py", "publish_event; worker coordination", persistence="Valkey streams/transient keys", test="backend/tests/test_health.py", gap="Live runtime proof was blocked before application startup.", authoritative="Platform Mongo/Temporal"),
    feature("OWN-07", "Bay Assignment", "Platform SQL bay ownership", "Platform SQL owns bay configuration, reservations, and assignments.", SOURCE, "backend/src/return_platform/operations/sql_business_state.py; infra/sqlserver/init/003_production_return_platform.sql", "list_bay_candidates; reserve_and_assign_handling_unit", persistence="platform.bay_configuration, platform.bay_reservation, platform.bay_assignment", test="backend/tests/test_bay_assignment.py"),
    feature("OWN-08", "Prompt Safety", "No AI-generated OMC SQL", "AI cannot generate or execute arbitrary SQL against OMC.", SOURCE, "backend/src/return_platform/ai_gateway/service.py", "AIGatewayService.evaluate", test="backend/tests/test_ai_gateway_policy.py", security="Fixed task prompts, allowlisted inputs, exact response schema, no SQL tool binding.", gap="No deployment-level OMC credential separation proof.", risk="MEDIUM"),
    feature("OWN-09", "Data Console", "Generic write isolation from OMC", "Data Console cannot mutate production OMC tables.", UNSAFE, "backend/src/return_platform/data_platform/ai_studio.py", "DIRECT_SQL_ASSETS; AIStudioService._apply_sql", route="POST /data-console/v1/ai-studio/proposals/{id}/apply", persistence="Configured SQL Server database", gap="Generic proposal apply performs INSERT/UPDATE using the same configurable SQL connection; safety depends on catalog/config naming rather than a physically separate credential/host boundary.", risk="CRITICAL", action="Hard-separate sandbox SQL credentials and reject OMC hosts/schemas in the write service."),
    feature("RMA-01", "RMA/OMC", "RMA versus RGA semantics", "Customer Return/RMA is separate from downstream vendor RGA.", VERIFIED, "backend/src/return_platform/dependency_simulation/service.py; backend/src/return_platform/workflows/production_return_state.py", "DependencySimulationService._omc/_lsi; apply_production_return_event", test="backend/tests/test_dependency_simulation.py::test_rga_requires_rtv_and_then_vendor_credit_can_complete", workflow="RETURN_CREATION then VENDOR_RECOVERY", authoritative="OMC"),
    feature("RMA-02", "Customer Resolution", "Independent customer resolution", "Customer refund/resolution can complete independently of product/vendor dimensions.", VERIFIED, "backend/src/return_platform/workflows/production_return_state.py", "apply_production_return_event", test="backend/tests/test_production_return_state.py", workflow="CUSTOMER_RESOLUTION"),
    feature("RMA-03", "Product Resolution", "Independent product resolution", "Product disposition is tracked separately from customer resolution.", VERIFIED, "backend/src/return_platform/workflows/production_return_state.py", "ProductionReturnWorkflowState.product_disposition_complete", test="backend/tests/test_production_return_state.py", workflow="PRODUCT_DISPOSITION"),
    feature("RMA-04", "Vendor Recovery", "Vendor recovery non-blocking for customer completion", "Customer-facing completion may precede vendor recovery; full closure waits for required recovery.", SOURCE, "backend/src/return_platform/workflows/production_return_state.py", "_advance_stage; apply_production_return_event", test="backend/tests/test_production_return_state.py", gap="No dedicated customer-facing completion timestamp/API state separate from workflow stage was runtime-proven."),
    feature("DISC-01", "Order Discovery", "Partial multi-anchor discovery", "Discover by name, phone, email, invoice/order, tracking, product, and location with strong-anchor validation.", PARTIAL, "backend/src/return_platform/operations/associate_flow.py; backend/config/returns/production.yaml", "AssociateConversationService._graph_candidates/_source_documents", route="POST /api/v1/associate-returns/conversations", persistence="associate_conversations; discovery_snapshots", test="backend/tests/agents/test_return_agents.py", screen="/associate/returns", gap="Invoice and location are not configured AnchorType values; strong-anchor enforcement is configuration/scoring rather than a hard request precondition.", risk="HIGH"),
    feature("DISC-02", "Order Discovery", "Graph-first targeted fallback", "Query Neo4j first, fall back narrowly to Source Mongo, then refresh the projection.", SOURCE, "backend/src/return_platform/operations/associate_flow.py", "_graph_candidates; _source_documents; _targeted_graph_upsert", test="backend/tests/test_customer_graph_projection_materializer.py"),
    feature("DISC-03", "Order Discovery", "FergusonHome W and web-to-Trilogie", "Recognize W orders and resolve to exact Trilogie order/line.", SOURCE, "backend/src/return_platform/agents/order_discovery.py; backend/config/returns/production.yaml", "OrderDiscoveryAgent._source; source_resolution", test="backend/tests/agents/test_return_agents.py"),
    feature("DISC-04", "Order Discovery", "Candidate scoring and ambiguity", "Apply weights/conflict penalties/thresholds, explain evidence, ask next question, and never auto-select.", VERIFIED, "backend/src/return_platform/agents/order_discovery.py", "OrderDiscoveryAgent.assess", test="backend/tests/agents/test_return_agents.py", screen="/associate/returns"),
    feature("DISC-05", "Order Discovery", "Exact line confirmation and immutable snapshot", "Associate confirms one exact order line and an immutable discovery snapshot is stored.", SOURCE, "backend/src/return_platform/operations/associate_flow.py", "confirm; _persist_discovery_snapshot", route="POST /api/v1/associate-returns/conversations/{id}/confirm", persistence="discovery_snapshots", gap="Mongo update uses upsert rather than database-enforced immutable write-once semantics.", risk="MEDIUM"),
    feature("DISC-06", "Order Discovery", "Stale graph detection", "Detect stale graph evidence before trusting it.", PARTIAL, "backend/src/return_platform/operations/associate_flow.py", "_graph_candidates", gap="graph_synced_at is written, but no configured freshness threshold is enforced in discovery.", risk="HIGH", action="Enforce graph freshness configuration and persist fallback reason."),
    feature("INTAKE-01", "Associate Intake", "Typed item reason/condition/quantity", "Capture reason, condition, partial quantity and prevent quantity above shipped eligibility.", VERIFIED, "backend/src/return_platform/operations/associate_flow.py; backend/src/return_platform/agents/return_workflow.py", "ReturnDetailsRequest; submit_details", route="POST /api/v1/associate-returns/conversations/{id}/details", persistence="operational_return_items; return_request_snapshots", test="backend/tests/agents/test_return_agents.py", screen="/associate/returns"),
    feature("INTAKE-02", "Associate Intake", "Handling units and physical metadata", "Support package/pallet/crate/bundle/loose items, dimensions, weight, and item binding.", PARTIAL, "backend/src/return_platform/operations/repository.py; backend/src/return_platform/operations/associate_flow.py", "persist_return_intake_records", persistence="handling_units", gap="Intake UI accepts package count, not a typed set of per-unit handling types/dimensions/weights and explicit many-to-many item binding.", risk="HIGH"),
    feature("INTAKE-03", "Associate Intake", "Photo metadata and future OCR/image contract", "Require photo metadata where applicable; current phase does not depend on OCR/images.", PARTIAL, "backend/src/return_platform/agents/return_workflow.py; backend/config/returns/production.yaml", "photoEvidenceRequired; extensions", configuration="extensions.ocr_processing=false; image_processing=false", gap="Attachment IDs are accepted, but required metadata schema is not defined.", risk="MEDIUM"),
    feature("INTAKE-04", "Associate Intake", "Branch/Associate contact and versioned Support snapshot", "Capture contacts and store a versioned Support request snapshot.", PARTIAL, "backend/src/return_platform/operations/associate_flow.py; backend/src/return_platform/operations/return_support/service.py", "ReturnDetailsRequest; CreateSupportWorkItemRequest", persistence="return_request_snapshots; support_work_items", gap="Associate ID/branch ID exist, but branch and Associate contact details are not a complete typed contact contract.", risk="MEDIUM"),
    feature("SAFE-01", "Security", "Branch staging safety invariants", "Require return tag, forbid manufacturer-box marking and branch inventory addition.", VERIFIED, "backend/src/return_platform/operations/physical/service.py", "BranchStagingService.stage", route="POST /api/v1/returns/{session_id}/branch-staging", persistence="branch_staging_records; handling_units", test="backend/tests/test_stage4_schema_and_seed_contracts.py"),
    feature("SAFE-02", "Parcel", "Staging before branch handoff", "Branch staging precedes label/freight handoff where applicable and label does not imply dispatch.", PARTIAL, "backend/src/return_platform/operations/physical/service.py; backend/src/return_platform/dependency_simulation/service.py", "BranchStagingService; _parcel", gap="Simulator label operation is not coupled to branch staging or package-ready evidence.", risk="HIGH"),
    feature("SUP-01", "Returns Support", "Internal queue and shared thread", "Internal queue supports assignment, acknowledgment, clarification, Associate response, versioned messages, and idempotent requests.", SOURCE, "backend/src/return_platform/operations/return_support/service.py", "ReturnSupportService; SupportAction", route="/api/v1/return-support/work-items and messages", persistence="support_work_items; support_messages; integration_outbox", test="backend/tests/operations/return_support/test_provider_architecture.py", screen="Missing required /return-support/workbench"),
    feature("SUP-02", "Returns Support", "Return creation/readback/instructions/resolution", "Support requests RMA/Return, stores authoritative readback, issues shipping instructions, and records customer resolution separately.", SOURCE, "backend/src/return_platform/operations/return_support/service.py", "apply_action", route="POST /api/v1/return-support/work-items/{id}/actions", persistence="support_work_items; omc_command_records; shipping_instructions", gap="No live OMC readback proof and no dedicated required workbench UI.", risk="BLOCKER"),
    feature("SUP-03", "Returns Support", "Optional external mirroring", "External ticket mirroring is optional and AI cannot fabricate IDs/acknowledgments.", SOURCE, "backend/src/return_platform/operations/return_support/providers/factory.py; backend/config/returns/production.yaml", "build_return_support_provider", configuration="support.external_mirror_enabled=false", security="External IDs must be provider/outbox evidence."),
    feature("PAR-01", "Parcel", "Parcel milestone separation", "Separate LABEL_CREATED, PACKAGE_READY, CARRIER_ACCEPTED, IN_TRANSIT, DELIVERED, RECEIVED.", PARTIAL, "backend/config/dependency_simulation.yaml; backend/src/return_platform/dependency_simulation/service.py", "_parcel", persistence="dependency_simulation_operations; shipment_events", test="backend/tests/test_dependency_simulation.py::test_parcel_label_does_not_imply_carrier_acceptance", gap="Simulator uses PACKAGE_ACCEPTED and has no distinct PACKAGE_READY and CARRIER_ACCEPTED states.", risk="HIGH"),
    feature("PAR-02", "Parcel", "Parcel exception lifecycle", "Handle no scan, lost, damaged, delivery exception, return-to-sender, void and reissue.", PARTIAL, "backend/src/return_platform/dependency_simulation/service.py", "_parcel; SIMULATE_EXCEPTION", gap="Generic exception plus void/reissue exist; named exception states and recovery transitions are incomplete.", risk="HIGH"),
    feature("PAR-03", "Parcel", "Package-label identity", "Explicitly bind label/tracking to the correct handling unit.", PARTIAL, "backend/src/return_platform/operations/repository.py; backend/src/return_platform/operations/physical/service.py", "handling_units trackingNumber index; register_document", gap="No atomic package-label confirmation command/state.", risk="HIGH"),
    feature("FRT-01", "Freight/TMS", "Heavy pickup assessment", "Capture address/contact, weight/dimensions/pallets/equipment/access/windows.", PARTIAL, "backend/config/returns/production.yaml; backend/src/return_platform/agents/return_workflow.py", "heavy_pickup_required_fields; ReturnWorkflowAgent.assess", gap="Dock/forklift/lift-gate/flatbed/crane/access restrictions are not all typed required fields.", risk="HIGH"),
    feature("FRT-02", "Freight/TMS", "Freight state separation", "Keep BOL creation, tender, booking, appointment, arrival, pickup and tracking distinct.", SIMULATED, "backend/src/return_platform/dependency_simulation/service.py", "_freight", test="backend/tests/test_dependency_simulation.py::test_freight_tender_booking_and_pickup_are_separate", persistence="dependency_simulation_operations"),
    feature("FRT-03", "Pickup", "No-show and rescheduling", "Record failed/no-show pickup and allow rescheduling.", SIMULATED, "backend/src/return_platform/dependency_simulation/service.py", "FAIL_PICKUP; RESCHEDULE_PICKUP", test="backend/tests/test_dependency_simulation.py::test_freight_tender_booking_and_pickup_are_separate"),
    feature("PATH-01", "Parcel", "BRANCH_PARCEL path", "Branch parcel reaches full closure with staging, parcel handoff, receipt, resolutions and optional vendor recovery.", SIMULATED, "backend/src/return_platform/api/dependency_simulator.py", "run_e2e(BRANCH_PARCEL)", route="POST /api/v1/dependency-simulator/e2e/{session}/run", test="backend/tests/test_dependency_simulation.py::test_simulated_branch_parcel_events_fully_close_production_state_machine", gap="Proof is an in-process state-machine test; live stack E2E did not run.", risk="HIGH"),
    feature("PATH-02", "Freight/TMS", "OFFSITE_HEAVY path", "Offsite heavy freight reaches full closure.", PARTIAL, "backend/src/return_platform/api/dependency_simulator.py", "run_e2e(OFFSITE_HEAVY)", route="POST /api/v1/dependency-simulator/e2e/{session}/run", test="scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY", gap="Script exists but full-stack execution was blocked; branch LTL is reused as method and readiness inputs are incomplete.", risk="BLOCKER"),
    feature("PATH-03", "Freight/TMS", "BRANCH_LTL path", "Dedicated branch LTL scenario with staging and freight lifecycle.", MISSING, "No dedicated scenario found", "None", gap="Only BRANCH_PARCEL and OFFSITE_HEAVY are accepted by the E2E script.", risk="BLOCKER"),
    feature("PATH-04", "Parcel", "OFFSITE_PARCEL path", "Product remains offsite, no branch staging, approved parcel instructions and handoff evidence.", MISSING, "No dedicated scenario found", "None", gap="Method enum exists but no dedicated workflow/E2E path.", risk="BLOCKER"),
    feature("PATH-05", "Vendor Recovery", "DIRECT_VENDOR path", "Explicit direct-vendor authorization without inapplicable carrier/bay steps.", MISSING, "Configuration enum only", "NormalizedReturnMethod.DIRECT_VENDOR", gap="No dedicated workflow/E2E scenario or authorization contract.", risk="BLOCKER"),
    feature("PATH-06", "Product Resolution", "NO_PHYSICAL_RETURN path", "Explicit field-scrap/customer-keep/no-return authorization with inapplicable physical requirements disabled.", PARTIAL, "backend/src/return_platform/workflows/production_return_state.py", "PHYSICAL_RETURN_NOT_REQUIRED", gap="Generic event exists, but no dedicated path orchestration, field-scrap/customer-keep authorization, UI, or E2E.", risk="BLOCKER"),
    feature("LSI-01", "LSI", "LSI simulator lifecycle", "Simulate authorization ack, receipt, license plate, disposition, processing, lot, RGA, debit/credit and closure.", SIMULATED, "backend/src/return_platform/dependency_simulation/service.py", "_lsi", persistence="dependency_simulation_operations", test="backend/tests/test_dependency_simulation.py::test_rga_requires_rtv_and_then_vendor_credit_can_complete", screen="/system/dependency-simulator/lsi"),
    feature("LSI-02", "LSI", "Live LSI integration and reconciliation", "Consume live file/API with duplicate protection, reconciliation, quarantine and manual review.", MISSING, "No live LSI file/API adapter found", "None", gap="Only simulator operations and generic trusted production events exist.", risk="BLOCKER"),
    feature("WH-01", "Warehouse", "Receipt/inspection/discrepancy/license plate/disposition", "Warehouse handles receipt, inspection, discrepancy, license plate and product disposition without overriding LSI.", PARTIAL, "backend/src/return_platform/operations/warehouse/service.py; backend/src/return_platform/api/warehouse_placement.py", "WarehousePlacementService", route="/api/v1/warehouse/returns", persistence="platform bay SQL; Mongo evidence", gap="No dedicated required warehouse screen and live LSI authority is absent.", risk="BLOCKER"),
    feature("BAY-01", "Bay Assignment", "Advisory recommendation and atomic reservation", "Recommend compatible/capacity-safe bay only after receipt; reserve/assign atomically; hold/overflow/release/expiry.", PARTIAL, "backend/src/return_platform/agents/bay_assignment.py; backend/src/return_platform/operations/sql_business_state.py", "BayAssignmentAgent.assess; reserve_and_assign_handling_unit", test="backend/tests/test_bay_assignment.py", gap="Atomic reserve/assign exists; release/expiry worker and complete overflow/hold lifecycle were not found.", risk="HIGH"),
    feature("FDB-01", "Feedback Learning", "Governed feedback recommendations", "Record corrections, delays, failures, overrides, mismatches, require human review, never auto-change policy.", PARTIAL, "backend/src/return_platform/operations/feedback_service.py; backend/src/return_platform/agents/feedback.py", "FeedbackLearningService.record; FeedbackLearningAgent.assess", persistence="feedback_learning_records; platform.feedback_recommendation", test="backend/tests/test_feedback_learning.py", gap="Only a subset of required metrics is calculated; corrections, package-label mismatch, refund delay and bay override coverage is incomplete.", risk="MEDIUM"),
    feature("AGT-01", "Order Discovery", "Order Discovery Agent", "Typed advisory agent ranks candidates and cannot auto-confirm.", VERIFIED, "backend/src/return_platform/agents/order_discovery.py", "OrderDiscoveryAgent.assess", route="POST /api/v1/return-agents/order-discovery/assess", persistence="agent_decisions", test="backend/tests/agents/test_return_agents.py", screen="/associate/returns"),
    feature("AGT-02", "Associate Intake", "Return Workflow Agent", "Typed advisory agent validates intake and drafts Support request.", VERIFIED, "backend/src/return_platform/agents/return_workflow.py", "ReturnWorkflowAgent.assess", route="POST /api/v1/return-agents/return-workflow/assess", persistence="agent_decisions", test="backend/tests/agents/test_return_agents.py", screen="/associate/returns"),
    feature("AGT-03", "RMA/OMC", "Return Fulfillment Agent", "Normalize authoritative facts without creating them.", SOURCE, "backend/src/return_platform/agents/fulfillment.py", "ReturnFulfillmentAgent.assess", route="POST /api/v1/return-agents/fulfillment/assess", persistence="agent_decisions", test="backend/tests/agents/test_return_agents.py", screen="Missing /operations/return-agents", gap="API-tested source exists; no dedicated required UI or live authoritative inputs."),
    feature("AGT-04", "Bay Assignment", "Bay Assignment Agent", "Advisory compatibility/capacity ranking; deterministic SQL owns assignment.", VERIFIED, "backend/src/return_platform/agents/bay_assignment.py", "BayAssignmentAgent.assess", route="POST /api/v1/return-agents/bay-assignment/assess", persistence="agent_decisions; platform bay SQL", test="backend/tests/agents/test_return_agents.py"),
    feature("AGT-05", "Feedback Learning", "Feedback Learning Agent", "Review-only recommendations; no automatic production rule changes.", VERIFIED, "backend/src/return_platform/agents/feedback.py", "FeedbackLearningAgent.assess", route="POST /api/v1/return-agents/feedback/assess", persistence="agent_decisions; feedback_learning_records", test="backend/tests/agents/test_return_agents.py"),
    feature("SIM-01", "Dependency Simulators", "OMC simulator", "Deterministic persisted/idempotent OMC state machine with non-production IDs and business guards.", PARTIAL, "backend/src/return_platform/dependency_simulation/service.py", "_omc", persistence="dependency_simulation_operations", test="backend/tests/test_dependency_simulation.py", screen="/system/dependency-simulator/omc", gap="Required SET_RETURN_METHOD operation is absent from configuration/service.", risk="HIGH"),
    feature("SIM-02", "Dependency Simulators", "Parcel simulator", "Deterministic label/tracking/exception simulator with operation history and signals.", PARTIAL, "backend/src/return_platform/dependency_simulation/service.py", "_parcel", persistence="dependency_simulation_operations", test="backend/tests/test_dependency_simulation.py", screen="/system/dependency-simulator/parcel", gap="Required PACKAGE_READY and CARRIER_ACCEPTED separation is absent.", risk="HIGH"),
    feature("SIM-03", "Dependency Simulators", "Freight simulator", "Deterministic quote/BOL/tender/booking/appointment/pickup/tracking/failure simulator.", SIMULATED, "backend/src/return_platform/dependency_simulation/service.py", "_freight", persistence="dependency_simulation_operations", test="backend/tests/test_dependency_simulation.py", screen="/system/dependency-simulator/freight"),
    feature("SIM-04", "Dependency Simulators", "LSI simulator", "Deterministic LSI warehouse/vendor-recovery simulator with RTV/RGA guards.", SIMULATED, "backend/src/return_platform/dependency_simulation/service.py", "_lsi", persistence="dependency_simulation_operations", test="backend/tests/test_dependency_simulation.py", screen="/system/dependency-simulator/lsi"),
    feature("SIM-05", "Security", "Simulator production isolation", "Simulation cannot start in production and is visibly marked.", VERIFIED, "backend/src/return_platform/configuration/settings.py; backend/src/return_platform/api/dependency_simulator.py", "Settings.validate_cross_field_constraints; _service; _simulation_header", test="backend/tests/test_dependency_simulation.py", security="Production startup validation and HTTP simulation marker."),
    feature("AI-01", "AI Gateway", "Provider/model/key lists and safe route IDs", "Normalize lists, reject blanks/duplicates, expand provider/model/credential routes, never expose raw credentials.", VERIFIED, "backend/src/return_platform/configuration/settings.py; backend/src/return_platform/ai_gateway/routing.py", "parse_key_list; parse_model_list; build_routes", test="backend/tests/test_ai_gateway_routing.py::test_settings_accept_key_and_model_lists"),
    feature("AI-02", "AI Gateway", "Complexity task registry", "Task registry fixes LIGHTWEIGHT/STANDARD tiers; simulator cannot escalate.", VERIFIED, "backend/config/ai_gateway.yaml; backend/src/return_platform/ai_gateway/service.py", "tasks; AIGatewayService.evaluate", test="scripts/validate_stage4n_ai_gateway.py", screen="Missing /ai-gateway/tasks"),
    feature("AI-03", "AI Gateway", "Key/model/provider failover", "Rotate key, then model, then provider within tier, then deterministic fallback.", VERIFIED, "backend/src/return_platform/ai_gateway/routing.py; backend/src/return_platform/ai_gateway/service.py", "AIRoutePool.candidates; AIGatewayService.evaluate", test="backend/tests/test_ai_gateway_routing.py; scripts/run_stage4n_ai_simulator_e2e.sh"),
    feature("AI-04", "AI Gateway", "Bounded retries and deadline", "Bound attempts/backoff and enforce per-route timeout/global deadline.", VERIFIED, "backend/src/return_platform/ai_gateway/service.py; backend/config/ai_gateway.yaml", "AIGatewayService.evaluate", test="backend/tests/test_ai_gateway_routing.py"),
    feature("AI-05", "AI Gateway", "Rate limiting", "Application/tier/provider/model/credential/route/task/session/user/concurrency limits are enforced and distributed where required.", PARTIAL, "backend/src/return_platform/ai_gateway/routing.py; backend/src/return_platform/operations/repository.py", "AIRoutePool.try_acquire; consume_ai_quota", persistence="Process-local AIRoutePool plus Mongo application quota", gap="No task/session/user limits; route/tier/provider/model/credential circuits and counters are process-local, not Valkey-distributed.", risk="BLOCKER"),
    feature("AI-06", "AI Gateway", "Circuit breakers", "Credential/model/provider/route circuits isolate failures and are distributed.", PARTIAL, "backend/src/return_platform/ai_gateway/routing.py", "AIRoutePool.record_failure", gap="Circuit state is explicitly process-local and lost on restart; Retry-After is not consumed.", risk="BLOCKER"),
    feature("AI-07", "Prompt Safety", "Prompt injection/domain/action firewall", "Block injection, encoded instructions, secrets, unauthorized actions and unrelated domains with deterministic response.", PARTIAL, "backend/src/return_platform/ai_gateway/safety.py; backend/src/return_platform/ai_gateway/service.py", "inspect_input; inspect_output", test="backend/tests/test_ai_gateway_routing.py::test_prompt_injection_is_blocked_before_provider_dispatch; test_domain_firewall_rejects_unrelated_request", gap="Legal and broad general-knowledge detection are absent; deterministic domain text is returned inside structured fallback but not proven for every unrelated request.", risk="HIGH"),
    feature("AI-08", "Prompt Safety", "Exact AI schemas and non-authority", "Reject unknown fields/invalid schemas/sizes and provide no direct authoritative tools.", VERIFIED, "backend/src/return_platform/ai_gateway/service.py", "_redact_and_validate; _parse_response", test="backend/tests/test_ai_gateway_policy.py"),
    feature("AI-09", "AI Metrics", "Durable attempt metrics", "Persist all attempt routing, safety, schema, token, cost, digest, timing and fallback fields with pagination/aggregation/UI.", PARTIAL, "backend/src/return_platform/ai_gateway/models.py; backend/src/return_platform/operations/repository.py", "AIUsageAttemptView; insert_ai_attempt_metric", route="GET /api/v1/ai-gateway/metrics and /metrics/summary", persistence="ai_gateway_attempt_metrics", screen="Missing /ai-gateway/metrics", gap="failureReason, schemaResult and explicit simulated/live marker are missing; UI route is absent; pagination is limit-only.", risk="HIGH"),
    feature("INF-01", "Infrastructure", "Container configuration paths", "Backend and all workers start from the same image with valid return, dependency-simulation and AI configuration paths.", UNSAFE, "backend/src/return_platform/configuration/settings.py; compose.yaml; backend/Dockerfile", "DEFAULT_RETURN_CONFIGURATION_PATH; DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH; DEFAULT_AI_GATEWAY_CONFIGURATION_PATH", test="docker compose --profile containerized-app up -d --build", gap="Backend and return-orchestrator restart: installed-package defaults resolve under /usr/local/lib/python3.13/config while image configuration is copied to /app/config; Compose overrides catalog/schema only.", risk="BLOCKER", action="Set all configuration path environment variables to /app/config paths and add a container startup test."),
    feature("INF-02", "Infrastructure", "Concurrent Mongo index startup", "Multiple app/workers initialize indexes idempotently without startup races.", UNSAFE, "backend/src/return_platform/operations/repository.py", "OperationalRepository._ensure_event_deduplication_index", test="docker compose --profile containerized-app up -d --build", gap="Concurrent startup can list then drop the same legacy index; a worker failed with OperationFailure IndexNotFound code 27.", risk="BLOCKER", action="Make legacy index migration single-owner or tolerate IndexNotFound atomically; add a concurrent startup test."),
    feature("OBS-01", "Observability", "Worker heartbeat visibility", "Every worker publishes and exposes a heartbeat.", PARTIAL, "backend/src/return_platform/operations/repository.py; backend/scripts/run_return_workflow_worker.py", "heartbeat; worker_heartbeats", persistence="worker_heartbeats TTL", gap="Full application workers did not reach validated heartbeat inspection in this audit.", risk="HIGH"),
    feature("API-01", "Infrastructure", "OpenAPI/frontend alignment", "Registered backend routes and generated frontend contracts remain aligned.", PARTIAL, "openapi.json; frontend/src/api/generated/return-platform.d.ts; frontend/src/contracts", "FastAPI OpenAPI; manual TS contracts", test="scripts/check_openapi_drift.py", gap="Frontend operational clients use manually duplicated contracts and many backend APIs have no UI; drift command was not run in Linux due full-stack interruption.", risk="HIGH"),
    feature("DOC-01", "Documentation", "Accurate feature documentation", "Every feature has accurate architecture, business, API, UI, run, validation and recovery documentation.", UNSAFE, "README.md; docs/implementation/STAGE_4N_AI_GATEWAY_HARDENING.md", "AI Gateway screen and metric claims", gap="README/runbook claim /ai-gateway/routes, /tasks, /metrics and complete screens that are absent; infra.sh probe is requested but unsupported.", risk="HIGH", action="Correct completion claims and add per-feature operator/user documentation."),
    feature("TST-01", "Testing", "Static and unit quality gates", "Compile, lint, format, strict typing, backend/frontend tests and build pass on the same source state.", VERIFIED, "backend/pyproject.toml; frontend/package.json", "quality scripts", test="Linux audit run: 987 backend tests; 39 frontend tests; all static gates passed"),
    feature("TST-02", "Testing", "Full business/browser E2E", "All six business scenarios and real-stack browser/accessibility/restart tests pass.", MISSING, "scripts/run_stage4m_simulated_e2e.sh; frontend/tests/e2e/happy-path-real.spec.ts", "BRANCH_PARCEL|OFFSITE_HEAVY only", gap="Only two script scenarios exist; full stack start/seed did not complete during audit; required browser matrix was not run.", risk="BLOCKER"),
]


required_screens = [
    ("/associate/returns", "Returns Assistant", "Associate", "AssociateReturnsPage", "/api/v1/associate-returns/*", SOURCE, "No explicit permission-denied or partial-data state; no dedicated component unit test."),
    ("/operations/returns/:sessionId", "Operations return detail", "Operations", "Missing", "None", MISSING, "Required route absent."),
    ("/operations/return-agents", "Return agents", "Operations", "Missing", "/api/v1/return-agents/*", MISSING, "Backend API exists; route/page absent."),
    ("/return-support/workbench", "Returns Support workbench", "Returns Support", "Missing", "/api/v1/return-support/*", MISSING, "Support pages use different routes and legacy support APIs."),
    ("/logistics/returns", "Logistics returns", "Logistics", "Missing", "/api/v1/returns/*/pickup", MISSING, "Required route/page absent."),
    ("/warehouse/returns", "Warehouse returns", "Warehouse", "Missing", "/api/v1/warehouse/returns", MISSING, "Required route/page absent."),
    ("/tracking/returns", "Tracking returns", "Tracking", "Missing", "/api/v1/returns/*/events", MISSING, "Required route/page absent."),
    ("/system/integration-outbox", "Integration outbox", "Audit/System", "Missing", "/api/v1/integration-outbox", MISSING, "Backend read API exists; route/page absent."),
    ("/system/dependencies", "Dependencies", "System", "DependenciesPage", "/api/v1/system/dependencies", SOURCE, "No permission-denied/partial/retry state."),
    ("/system/dependency-simulator", "Dependency simulator", "System", "OverviewPage", "/api/v1/dependency-simulator/summary", SOURCE, "Unit/browser coverage not dedicated."),
    ("/system/dependency-simulator/omc", "OMC simulator", "System", "OmcPage", "/api/v1/dependency-simulator/operations", SOURCE, "OMC operation set incomplete."),
    ("/system/dependency-simulator/parcel", "Parcel simulator", "System", "ParcelPage", "/api/v1/dependency-simulator/operations", SOURCE, "Parcel state set incomplete."),
    ("/system/dependency-simulator/freight", "Freight simulator", "System", "FreightPage", "/api/v1/dependency-simulator/operations", SOURCE, "Browser proof unavailable."),
    ("/system/dependency-simulator/lsi", "LSI simulator", "System", "LsiPage", "/api/v1/dependency-simulator/operations", SOURCE, "Browser proof unavailable."),
    ("/system/dependency-simulator/ai-metrics", "Simulator AI metrics", "System", "AiMetricsPage", "/api/v1/dependency-simulator/ai-metrics", SOURCE, "Browser proof unavailable."),
    ("/system/dependency-simulator/operations/:operationId", "Simulator operation", "System", "OperationDetailPage", "/api/v1/dependency-simulator/operations/{id}", SOURCE, "Browser proof unavailable."),
    ("/ai-gateway/requests", "AI requests", "AI operator", "AIRequestsPage", "/api/v1/ai-gateway/requests", SOURCE, "No permission-denied/partial/retry state."),
    ("/ai-gateway/routes", "AI routes", "AI operator", "Missing", "/api/v1/ai-gateway/routes", MISSING, "README claims route; frontend route absent."),
    ("/ai-gateway/tasks", "AI tasks", "AI operator", "Missing", "/api/v1/ai-gateway/tasks", MISSING, "README claims route; frontend route absent."),
    ("/ai-gateway/metrics", "AI metrics", "AI operator", "Missing", "/api/v1/ai-gateway/metrics", MISSING, "README claims route; frontend route absent."),
    ("/ai-gateway/safety", "AI safety", "AI operator", "Missing", "/api/v1/ai-gateway/safety-test", MISSING, "Required route/page absent."),
    ("/ai-gateway/simulator", "AI simulator", "AI operator", "AISimulatorPage", "/api/v1/ai-gateway/simulator", SOURCE, "Mutation error exists; no empty/partial/permission state."),
    ("/ai-gateway/interceptions", "AI interceptions", "AI operator", "AIInterceptionsPage", "/api/v1/ai-gateway/requests and settings", SOURCE, "No explicit permission-denied/partial/retry state."),
]

screens = [
    {
        "Route": route,
        "Screen": name,
        "Owner role": role,
        "Component": component,
        "API endpoint": api,
        "Persistence/source": "Backend source shown in API endpoint" if api != "None" else "None",
        "Actions": "Present where component exists; backend write role checks exist" if component != "Missing" else "None",
        "Loading": component != "Missing",
        "Empty": component != "Missing",
        "Partial": False,
        "Error": component != "Missing",
        "Authorization": "Server-side role dependency on backend route" if api != "None" else "None",
        "Simulation marker": route.startswith("/system/dependency-simulator"),
        "Unit test": "No dedicated page test found",
        "Browser test": "No route-specific real-stack proof in this audit",
        "Documentation": "README.md" if route in Path(ROOT / "README.md").read_text(encoding="utf-8") else "Missing",
        "Classification": status,
        "Gap": gap,
    }
    for route, name, role, component, api, status, gap in required_screens
]


agents = [
    {"Agent": f["Feature"], "Classification": f["Classification"], "Source": f["Source evidence"], "Typed input/output": True, "Boundary": f["Required behavior"], "Prohibited actions": "No authoritative facts or arbitrary SQL", "Configuration": "backend/config/returns/production.yaml", "Invocation": f["API"], "Persistence": f["Persistence"], "Metrics": "agent_decisions", "Tests": f["Test"], "UI": f["Screen"], "Fallback": "Deterministic/human review", "Authority": "Advisory"}
    for f in features if f["Feature ID"].startswith("AGT-")
]

workflow_scenarios = [
    {"Scenario": "BRANCH_PARCEL", "Classification": SIMULATED, "Starting state": "INTAKE", "Required inputs": "confirmed discovery, return details, Support/RMA, branch handling/staging", "Workflow stages": "INTAKE→SUPPORT→RETURN_CREATION→PHYSICAL_RETURN_SETUP→RETURN_SHIPMENT→RECEIPT→CUSTOMER_RESOLUTION→PRODUCT_DISPOSITION→WAREHOUSE_PROCESSING→VENDOR_RECOVERY→FULLY_CLOSED", "Signals": "record_production_event updates from simulator bridge", "Activities": "None in production v2", "External/simulated operations": "OMC+PARCEL+LSI", "Authoritative evidence": "Simulated identifiers and Temporal event state", "Terminal customer state": "complete", "Terminal physical state": "complete", "Terminal warehouse state": "complete", "Terminal vendor state": "complete when requested", "Full-closure condition": "all applicable dimensions terminal", "Gap": "No live-stack E2E result."},
    {"Scenario": "OFFSITE_HEAVY", "Classification": PARTIAL, "Starting state": "INTAKE", "Required inputs": "confirmed discovery, heavy pickup assessment", "Workflow stages": "Generic production stages", "Signals": "OMC/FREIGHT/LSI simulator bridge", "Activities": "None in production v2", "External/simulated operations": "OMC+FREIGHT+LSI", "Authoritative evidence": "Simulated only", "Terminal customer state": "designed complete", "Terminal physical state": "designed complete", "Terminal warehouse state": "designed complete", "Terminal vendor state": "designed complete", "Full-closure condition": "generic all-dimensions predicate", "Gap": "Live stack blocked; readiness fields incomplete."},
    *[
        {"Scenario": name, "Classification": MISSING if name in {"BRANCH_LTL", "OFFSITE_PARCEL", "DIRECT_VENDOR"} else PARTIAL, "Starting state": "Not dedicated", "Required inputs": "Not fully specified", "Workflow stages": "Generic event state only", "Signals": "No dedicated scenario signal matrix", "Activities": "None", "External/simulated operations": "Not dedicated", "Authoritative evidence": "None", "Terminal customer state": "Unproven", "Terminal physical state": "Unproven", "Terminal warehouse state": "Unproven", "Terminal vendor state": "Unproven", "Full-closure condition": "Generic predicate only", "Gap": "No dedicated orchestration and E2E."}
        for name in ["BRANCH_LTL", "OFFSITE_PARCEL", "DIRECT_VENDOR", "NO_PHYSICAL_RETURN"]
    ],
]

simulators = [
    {"Dependency": name, "Classification": status, "Module": "backend/src/return_platform/dependency_simulation/service.py", "Gateway interface": "SimulationRepository + SimulationTopicDispatcher", "Mode selection": f"PLATFORM_{name}_DEPENDENCY_MODE", "Deterministic state/IDs": True, "Idempotency": True, "Persistence/history": "dependency_simulation_operations", "Progression": "manual API; bounded E2E automation", "Failure scenarios": "Generic plus dependency-specific failures", "Temporal signalling": True, "Visible marker": True, "Production protection": True, "Tests": "backend/tests/test_dependency_simulation.py", "UI": f"/system/dependency-simulator/{name.lower()}", "Gap": gap}
    for name, status, gap in [
        ("OMC", PARTIAL, "SET_RETURN_METHOD absent."),
        ("PARCEL", PARTIAL, "PACKAGE_READY/CARRIER_ACCEPTED separation absent."),
        ("FREIGHT", SIMULATED, "Live TMS integration absent by design."),
        ("LSI", SIMULATED, "Live LSI/reconciliation absent by design."),
    ]
]

ai_gateway = [
    {k: f[k] for k in ["Feature ID", "Feature", "Classification", "Source evidence", "Test", "Screen", "Known gap", "Risk", "Recommended action"]}
    for f in features if f["Feature ID"].startswith("AI-")
]

api_matrix = [
    {"Area": area, "Router registration": True, "Path/method": path, "Request/response model": "Pydantic response_model present", "Error model": "APIResponse warnings envelope", "Authentication": "Global principal middleware", "Authorization": auth, "Idempotency": idem, "Persistence": persistence, "Audit": audit, "OpenAPI": True, "Frontend client": frontend, "Frontend contract": contract, "Tests": tests, "Classification": status, "Gap": gap}
    for area, path, auth, idem, persistence, audit, frontend, contract, tests, status, gap in [
        ("Associate returns", "/api/v1/associate-returns/*", "Associate/read roles", "Conversation and snapshot identifiers", "Mongo", "Messages/decisions/events", "frontend/src/api/associateReturns.ts", "Manual TS", "Agent/backend tests", SOURCE, "No dedicated frontend unit test."),
        ("Returns Support", "/api/v1/return-support/*", "Associate/collaboration/support roles", "Work-item idempotency and optimistic version", "Mongo/outbox", "Thread/outbox evidence", "No required workbench client", "Manual TS absent", "Provider architecture tests", PARTIAL, "Required workbench UI absent."),
        ("Physical operations", "/api/v1/returns/*", "Associate/logistics/read roles", "Event deduplication", "Mongo/SQL", "Operational events", "No dedicated logistics UI", "Manual TS partial", "No focused API E2E", PARTIAL, "Backend APIs have no required screens."),
        ("Warehouse", "/api/v1/warehouse/returns/*", "Warehouse role", "Reservation IDs", "SQL/Mongo", "Assignments/events", "No required warehouse UI", "Manual TS absent", "Bay tests", PARTIAL, "Required screen absent."),
        ("AI Gateway", "/api/v1/ai-gateway/*", "Read/write roles", "Trace IDs/digests", "Mongo", "Attempt metrics", "Requests/simulator/interceptions only", "Manual TS", "Routing/policy tests", PARTIAL, "Routes/tasks/metrics/safety screens absent."),
        ("Dependency simulator", "/api/v1/dependency-simulator/*", "Read/write roles and env guard", "Unique idempotencyKey", "Mongo", "Operation history", "frontend/src/api/dependencySimulator.ts", "Manual TS", "Simulation tests", SOURCE, "Operation/state gaps remain."),
        ("Integration outbox", "/api/v1/integration-outbox", "Audit role", "Outbox idempotency", "Mongo", "Outbox itself", "None", "None", "No browser test", PARTIAL, "Required screen absent."),
        ("Data Console writes", "/data-console/v1/ai-studio/proposals/{id}/apply", "Write role", "Proposal state", "Config-selected SQL/Mongo", "Audit collection", "Data Studio client", "Manual TS", "Schema/source tests", UNSAFE, "No hard physical isolation from production OMC connection."),
    ]
]

configuration_matrix = [
    {"Configuration": path, "Consumed by": symbol, "Validation": validation, "Snapshot/digest": digest, "Classification": status, "Gap": gap}
    for path, symbol, validation, digest, status, gap in [
        ("backend/config/returns/production.yaml", "load_return_configuration; agents; services", "Strict Pydantic", "Persisted return_configuration_snapshots", SOURCE, "Graph freshness and full heavy-equipment policy missing."),
        ("backend/config/dependency_simulation.yaml", "DependencySimulationService", "Strict Pydantic + required dependencies", "SHA logged", PARTIAL, "OMC SET_RETURN_METHOD absent; parcel milestones differ."),
        ("backend/config/ai_gateway.yaml", "AIGatewayService/AIRoutePool", "Strict task/limit config", "SHA logged", SOURCE, "Task/session/user limits not modeled."),
        ("backend/src/return_platform/configuration/settings.py", "Application startup", "Pydantic validators and production simulation guard", "Indirect environment snapshot", SOURCE, "Legacy single key/model fields coexist with list path."),
        ("compose.yaml", "Compose topology", "docker compose config", "Image/source state only", PARTIAL, "Single configurable SQL credential spans platform tables and potential generic write service."),
    ]
]

data_models = [
    {"Store": store, "Model/collection/table": name, "Definition/repository": source, "Indexes/idempotency": indexes, "Version/timestamps/audit": audit, "Startup/migration": migration, "Tests": tests, "Classification": status, "Gap": gap}
    for store, name, source, indexes, audit, migration, tests, status, gap in [
        ("Mongo", "operational_returns + return_sessions", "OperationalRepository + MongoReturnSessionRepository", "Unique idempotency/revision/event indexes", "Both versioned/audited differently", "App/worker startup", "session/event tests", PARTIAL, "Duplicate authoritative session models."),
        ("Mongo", "associate_conversations/messages/discovery_snapshots/return_request_snapshots", "AssociateConversationService", "Conversation/message/snapshot indexes", "Timestamps and snapshot digests", "Lazy ensure_indexes", "agent tests", SOURCE, "Snapshot immutability not database-enforced."),
        ("Mongo", "operational_return_items/handling_units/pickup_sites/pickup_requests/branch_staging_records", "OperationalRepository", "Unique business IDs and bindings", "Timestamps/events", "App startup", "schema tests", SOURCE, "Retention not defined."),
        ("Mongo", "support_work_items/support_messages", "ReturnSupportService", "Unique session/idempotency/thread sequence", "Optimistic version/timestamps", "App startup", "provider tests", SOURCE, "Legacy support_cases also exists."),
        ("Mongo", "shipping_instructions/shipment_events/omc_command_records/vendor_return_links/document_artifacts", "OperationalRepository", "Unique external IDs and source events", "Timestamps/events", "App startup", "schema tests", SOURCE, "Live integration proof absent."),
        ("Mongo", "dependency_simulation_operations/dependency_simulation_ai_metrics", "MongoSimulationRepository", "Unique idempotency plus query indexes", "Timestamps/history", "App startup", "simulation tests", SIMULATED, "No retention policy."),
        ("Mongo", "ai_gateway_traces/ai_gateway_attempt_metrics/ai_gateway_rate_limits", "OperationalRepository", "Trace/attempt/query/TTL indexes", "Timestamps/digests", "App startup", "AI tests", PARTIAL, "Attempt schema misses required audit fields; distributed route state absent."),
        ("Mongo", "feedback_learning_records", "FeedbackLearningService", "Unique session and review indexes", "Evidence digest/timestamp", "Lazy ensure_indexes", "feedback tests", PARTIAL, "Expected learning_feedback name differs; metric scope incomplete."),
        ("SQL", "platform.bay_configuration/reservation/assignment", "SQLBusinessStateRepository; migrations 002-004", "Constraints and indexes", "Created/updated/expiry fields", "Forward init SQL", "bay tests", SOURCE, "Release/expiry execution missing."),
        ("SQL", "dbo.return_requests/items/fulfillment/tracking", "SQLBusinessStateRepository; migrations 001-003", "Indexes and serializable writes", "row_version/timestamps", "Forward init SQL", "schema tests", PARTIAL, "Local duplicate of facts designated OMC-authoritative."),
    ]
]

docs_matrix = [
    {"Feature": item["Feature"], "Implementation present?": item["Classification"] not in {MISSING, DOC, CONFIG}, "Architecture documentation present?": "README" in item["Documentation"], "Business documentation present?": True, "Configuration documented?": item["Configuration"] != "None", "API documented?": item["API"] != "None", "UI documented?": item["Screen"] != "None", "Run command present?": True, "Validation command present?": True, "Failure/recovery documented?": item["Domain"] in {"Infrastructure", "Dependency Simulators", "AI Gateway"}, "Evidence present?": True, "Documentation accurate?": item["Feature ID"] not in {"DOC-01", "AI-09"}, "Classification": "CONFLICTING" if item["Feature ID"] == "DOC-01" else ("PARTIAL" if item["Classification"] not in {VERIFIED, SIMULATED} else "COMPLETE")}
    for item in features
]

tests = [
    {"Command": "python3 -m compileall -q backend/src backend/tests scripts", "Environment": "python:3.13-slim Linux container, Python 3.13.14", "Exit code": 0, "Duration": "not captured", "Result": "PASSED", "Details": "No output."},
    {"Command": "cd backend && ruff check .", "Environment": "Linux container, Ruff 0.15.21", "Exit code": 0, "Duration": "about 3s", "Result": "PASSED", "Details": "All checks passed."},
    {"Command": "cd backend && ruff format --check .", "Environment": "Linux container, Ruff 0.15.21", "Exit code": 0, "Duration": "about 3s", "Result": "PASSED", "Details": "246 files already formatted."},
    {"Command": "cd backend && mypy --strict src", "Environment": "Linux container, MyPy 2.3.0", "Exit code": 0, "Duration": "about 28s", "Result": "PASSED", "Details": "Success: no issues found in 172 source files."},
    {"Command": "cd backend && pytest", "Environment": "Linux container, Python 3.13.14, Pytest 9.1.1", "Exit code": 0, "Duration": "18.16s test time", "Result": "PASSED", "Details": "987 passed, 1 deprecation warning."},
    {"Command": "cd frontend && npm run lint", "Environment": "Windows host Node 24.14.0/npm 11.9.0 because no general WSL distro", "Exit code": 0, "Duration": "14.146s", "Result": "PASSED", "Details": "ESLint clean."},
    {"Command": "cd frontend && npm run typecheck", "Environment": "Windows host, rerun outside sandbox after node_modules/.tmp EPERM", "Exit code": 0, "Duration": "5.188s", "Result": "PASSED", "Details": "Initial sandbox attempt blocked; approved rerun passed."},
    {"Command": "cd frontend && npm run test", "Environment": "Windows host", "Exit code": 0, "Duration": "7.06s test time", "Result": "PASSED", "Details": "13 files, 39 tests passed."},
    {"Command": "cd frontend && npm run build", "Environment": "Windows host", "Exit code": 0, "Duration": "7.347s", "Result": "PASSED", "Details": "Vite production build and bundle mock-artifact check passed."},
    {"Command": "./scripts/run_stage4n_ai_simulator_e2e.sh", "Environment": "Linux container, Python 3.13.14", "Exit code": 0, "Duration": "about 7s", "Result": "PASSED", "Details": "9 validator checks passed; 5 focused tests passed, 6 deselected."},
    {"Command": "./scripts/infra.sh start (docker compose up -d --wait equivalent)", "Environment": "Docker Desktop Linux engine", "Exit code": 1, "Duration": "about 4m initial recovery", "Result": "FAILED", "Details": "Initial PostgreSQL/SQL/Neo4j health budget failure; dependencies later individually healthy. Retry still exited 1 because successful one-shot mongodb-rs-init was treated as exited."},
    {"Command": "./scripts/infra.sh probe", "Environment": "Repository script inspection", "Exit code": 2, "Duration": "not run", "Result": "BLOCKED", "Details": "scripts/infra.sh has no probe action; supported actions are start/full-containerized/stop/status/logs/reset/config."},
    {"Command": "docker compose --profile containerized-app up -d --build (containerized equivalent attempted for start_stage4m_simulation.sh)", "Environment": "Docker Desktop Linux engine", "Exit code": 1, "Duration": "about 6m including build/start", "Result": "FAILED", "Details": "Seed succeeded and core dependencies became healthy. Backend/orchestrator restart on wrong /usr/local/lib/python3.13/config paths; concurrent Mongo index migration also raised IndexNotFound code 27."},
    {"Command": "./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL", "Environment": "Full stack", "Exit code": None, "Duration": "not run", "Result": "BLOCKED", "Details": "Application profile did not reach validated readiness before evidence cutoff."},
    {"Command": "./scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY", "Environment": "Full stack", "Exit code": None, "Duration": "not run", "Result": "BLOCKED", "Details": "Application profile did not reach validated readiness before evidence cutoff."},
    {"Command": "BRANCH_LTL/OFFSITE_PARCEL/DIRECT_VENDOR/NO_PHYSICAL_RETURN business E2E", "Environment": "Source inspection", "Exit code": 2, "Duration": "not run", "Result": "BLOCKED", "Details": "run_stage4m_simulated_e2e.sh accepts only BRANCH_PARCEL and OFFSITE_HEAVY."},
    {"Command": "npm run test:e2e:real", "Environment": "Real full stack", "Exit code": None, "Duration": "not run", "Result": "BLOCKED", "Details": "Full stack not validated and required screens are absent."},
]

gaps = [
    {"Gap ID": f"GAP-{idx:03d}", "Feature ID": item["Feature ID"], "Severity": item["Risk"] if item["Risk"] in {"BLOCKER", "CRITICAL", "HIGH", "MEDIUM", "LOW"} else "MEDIUM", "Classification": item["Classification"], "Description": item["Known gap"], "Business impact": item["Required behavior"], "Technical impact": item["Known gap"], "Security impact": item["Security control"] if item["Classification"] == UNSAFE else "No direct exploit proven; control/evidence is incomplete.", "Evidence": item["Source evidence"], "Recommended fix": item["Recommended action"], "Target file/module": item["Source evidence"], "Required test": item["Test"], "Required documentation": item["Documentation"], "Dependency": item["Authoritative system"], "Estimated implementation order": idx}
    for idx, item in enumerate([f for f in features if f["Classification"] not in {VERIFIED, SIMULATED, NA}], start=1)
]

mandatory_answers = [
    ("1. Are all five business agents present and correctly bounded?", "Yes in source: all five have strict typed contracts, advisory language, API invocation and tests. Fulfillment lacks its required UI/live authoritative proof."),
    ("2. Are all six return paths implemented?", "No. BRANCH_PARCEL is simulated; OFFSITE_HEAVY is partial; BRANCH_LTL, OFFSITE_PARCEL and DIRECT_VENDOR lack dedicated orchestration/E2E; NO_PHYSICAL_RETURN is only a generic event path."),
    ("3. Can the branch-parcel scenario reach full closure?", "Yes in the deterministic in-process production-state test; not proven on the live stack."),
    ("4. Can the offsite-heavy scenario reach full closure?", "Not proven. Source E2E logic exists, but the live-stack scenario did not run."),
    ("5. Are RMA and RGA correctly separated?", "Yes in state/simulator rules; live OMC contract proof is missing."),
    ("6. Are customer and product resolutions independent?", "Yes in ProductionReturnWorkflowState."),
    ("7. Is vendor recovery non-blocking for customer completion?", "Yes logically; full closure still waits when recovery is required."),
    ("8. Is BOL tender separated from booking and pickup?", "Yes in the freight simulator and unit test."),
    ("9. Is package/handling-unit identity enforced?", "Partially. Unique handling units/tracking exist, but no atomic package-label confirmation state."),
    ("10. Are branch safety rules enforced?", "Yes by BranchStagingService."),
    ("11. Is Returns Support internal workflow complete?", "Source workflow is substantial, but no live OMC readback proof or required workbench screen; therefore no."),
    ("12. Are OMC, parcel, freight, and LSI simulators complete?", "No. OMC lacks SET_RETURN_METHOD; parcel lacks required PACKAGE_READY/CARRIER_ACCEPTED separation. Freight and LSI meet their listed operation sets."),
    ("13. Are simulators isolated from production?", "Yes by startup/API/service guards and visible headers/banner."),
    ("14. Does AI failure leave the main simulator flow working?", "Yes; Stage 4N validator and focused tests passed."),
    ("15. Are lightweight and standard model tiers enforced?", "Yes."),
    ("16. Are key and model lists supported?", "Yes."),
    ("17. Does failover rotate key, model, and provider correctly?", "Yes in deterministic tests."),
    ("18. Are retries bounded?", "Yes by maximumTotalAttempts and global deadline."),
    ("19. Is rate limiting implemented and is it distributed?", "Partially implemented; route/tier/provider/model/credential limits are process-local. Only an application quota uses durable Mongo. Task/session/user limits are absent."),
    ("20. Are circuit breakers implemented and distributed?", "Implemented but process-local, not distributed."),
    ("21. Is prompt injection blocked?", "Known patterns are blocked before dispatch; coverage is pattern-based."),
    ("22. Are out-of-domain questions rejected?", "Medical, financial, political and general-coding patterns are blocked; legal/general-knowledge coverage is incomplete."),
    ("23. Are exact output schemas enforced?", "Yes for registered gateway output and simulator narratives."),
    ("24. Can AI perform any authoritative action directly?", "No direct authoritative tool is bound to AIGatewayService."),
    ("25. Are all AI attempts and fallback metrics captured?", "No. Durable attempts exist, but required failureReason/schemaResult/live-vs-simulated fields and dedicated UI are incomplete."),
    ("26. Does every feature have accurate documentation?", "No; documentation claims AI routes/screens that do not exist and cites an unsupported infra probe action."),
    ("27. Does every screen have a dedicated route and real API wiring?", "No; 11 of 23 mandatory exact routes are absent."),
    ("28. Are loading, empty, partial, and error states present?", "Loading/empty/error exist on many implemented pages; partial-data, permission-denied and explicit retry states are generally absent."),
    ("29. Do all server-side role checks exist?", "Most audited backend routes have role dependencies; global auth exists. No screen-level claim is made for absent routes."),
    ("30. Do all quality gates pass?", "Static/unit gates pass, but infrastructure/full-stack/browser gates do not all pass."),
    ("31. Do all browser E2E scenarios pass?", "No; they were blocked and several required screens do not exist."),
    ("32. Can the application recover from API/worker restarts?", "Not proven."),
    ("33. Are any production flows still dependent on process-local memory?", "Yes: AI route rate/circuit/concurrency state is process-local."),
    ("34. Are OpenAPI and frontend contracts aligned?", "Not fully proven; manually duplicated TS contracts remain and many backend routes have no frontend consumer."),
    ("35. What exactly remains before LIVE_STACK_VALIDATED?", "Fix Compose one-shot wait/probe behavior; start all app/worker services; prove heartbeats; run all six scenario E2Es, restart/replay, real Playwright and accessibility; resolve route/API gaps."),
    ("36. What exactly remains before PRODUCTION_READY?", "Everything for LIVE_STACK_VALIDATED plus real OMC/carrier/TMS/LSI integrations, distributed AI limits/circuits, hard Source/OMC credential isolation, all mandatory screens/states, recovery/DR/performance/security/observability/deployment/rollback evidence."),
]


def dump_json(filename: str, items: Any, **metadata: Any) -> None:
    payload = {"audit": "STAGE_4O", "commit": COMMIT, "generatedAt": "2026-07-26T12:15:00+05:30", **metadata, "items": items}
    (OUT / filename).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def table(items: list[dict[str, Any]], columns: list[str]) -> str:
    def clean(value: Any) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    lines.extend("| " + " | ".join(clean(item.get(column, "")) for column in columns) + " |" for item in items)
    return "\n".join(lines)


def write_matrix(filename: str, title: str, intro: str, items: list[dict[str, Any]], columns: list[str]) -> None:
    (OUT / filename).write_text(f"# {title}\n\n{intro}\n\n{table(items, columns)}\n", encoding="utf-8")


inventory = {
    "repositoryRoot": DISPLAY_ROOT,
    "commit": COMMIT,
    "workingTreeBeforeAudit": "clean",
    "requirements": {"python": ">=3.13,<3.14", "node": ">=24,<25", "npm": ">=11,<12"},
    "counts": {"backendSourceFiles": 173, "backendTestFiles": 61, "frontendSourceFiles": 164, "frontendTestSpecs": 3, "scripts": 58, "docsFilesBeforeAudit": 140},
    "backendPackages": ["agents", "ai_gateway", "api", "canonical", "configuration", "data_console", "data_governance", "data_platform", "dependency_simulation", "operations", "security", "shared", "workers", "workflows"],
    "frontendPackages": ["api", "components", "contracts", "features/operations", "features/dependency-simulator", "features/data-console"],
    "agents": [a["Agent"] for a in agents],
    "workflows": ["ReturnWorkflow (legacy v1)", "ProductionReturnWorkflow (production v2)"],
    "workers": ["return-workflow-worker", "return-orchestrator", "outbox-publisher", "data-job-worker"],
    "infrastructureTopology": ["MongoDB platform/source databases", "Neo4j derived graph", "SQL Server local platform/sandbox schema", "Valkey", "Temporal + PostgreSQL", "FastAPI", "React/Vite/nginx"],
    "duplicateImplementations": ["operational_returns versus return_sessions", "operational_events versus return_session_audit/outbox events", "support_cases versus support_work_items/messages", "AI gateway attempt metrics versus dependency simulator AI metrics", "legacy ReturnWorkflow versus ProductionReturnWorkflow"],
    "placeholderOrEmpty": ["No empty Python modules beyond package __init__ files; required feature routes are absent rather than placeholders."],
    "staleOrConflictingDocs": ["README and Stage 4N docs list /ai-gateway/routes, /tasks and /metrics screens absent from frontend routes.", "Prompt/runbook requests scripts/infra.sh probe, but the script has no probe action.", "README uses SOURCE_VALIDATED accurately overall but screen-level completion claims conflict with source."],
}

dump_json("repository_inventory.json", [inventory])
(OUT / "repository_inventory.md").write_text(
    "# Repository Inventory\n\n"
    f"Audited root: `{DISPLAY_ROOT}`  \nCommit: `{COMMIT}`  \nInitial working tree: clean.\n\n"
    "## Toolchain and topology\n\n"
    f"- Python `>=3.13,<3.14`; Node `>=24,<25`; npm `>=11,<12`.\n"
    "- 173 backend source files, 61 backend test files, 164 frontend source files, 3 Playwright specs, 58 scripts, and 140 pre-existing docs files.\n"
    "- Runtime topology: MongoDB (platform/source DBs), Neo4j, SQL Server, Valkey, Temporal/PostgreSQL, FastAPI, and React/Vite/nginx.\n\n"
    "## Packages and runtime modules\n\n"
    f"- Backend: {', '.join(inventory['backendPackages'])}.\n"
    f"- Frontend: {', '.join(inventory['frontendPackages'])}.\n"
    f"- Agents: {', '.join(inventory['agents'])}.\n"
    f"- Workflows: {', '.join(inventory['workflows'])}.\n"
    f"- Workers: {', '.join(inventory['workers'])}.\n\n"
    "## Duplicate/conflicting implementations\n\n"
    + "\n".join(f"- {x}" for x in inventory["duplicateImplementations"])
    + "\n\n## Stale/conflicting documentation\n\n"
    + "\n".join(f"- {x}" for x in inventory["staleOrConflictingDocs"])
    + "\n",
    encoding="utf-8",
)

business = [f for f in features if not f["Feature ID"].startswith(("AGT-", "SIM-", "AI-", "DOC-", "TST-", "API-", "OBS-"))]
write_matrix("business_logic_matrix.md", "Business Logic Matrix", "Classifications are based on direct source and current audit runtime evidence.", business, ["Feature ID", "Feature", "Classification", "Source file path", "Class/function/component/config symbol", "API", "Persistence", "Test", "Missing pieces", "Risk"])
dump_json("business_logic_matrix.json", business)
write_matrix("agent_matrix.md", "Agent Matrix", "All five bounded agents exist; UI/runtime completeness differs.", agents, ["Agent", "Classification", "Source", "Typed input/output", "Boundary", "Invocation", "Persistence", "Tests", "UI", "Authority"])
dump_json("agent_matrix.json", agents)
write_matrix("workflow_state_matrix.md", "Workflow State Matrix", "The production-v2 workflow is a durable event state machine; only two simulator scenarios are exposed.", workflow_scenarios, ["Scenario", "Classification", "Starting state", "Required inputs", "Workflow stages", "Signals", "Activities", "Authoritative evidence", "Full-closure condition", "Gap"])
dump_json("workflow_state_matrix.json", workflow_scenarios)
write_matrix("dependency_simulator_matrix.md", "Dependency Simulator Matrix", "Simulator source is isolated from production, but two required operation/state sets are incomplete.", simulators, ["Dependency", "Classification", "Module", "Mode selection", "Deterministic state/IDs", "Idempotency", "Persistence/history", "Temporal signalling", "Production protection", "Tests", "UI", "Gap"])
dump_json("dependency_simulator_matrix.json", simulators)
write_matrix("ai_gateway_matrix.md", "AI Gateway Matrix", "Routing and safety tests pass; distributed safety and complete metrics/UI do not.", ai_gateway, ["Feature ID", "Feature", "Classification", "Source evidence", "Test", "Screen", "Known gap", "Risk", "Recommended action"])
dump_json("ai_gateway_matrix.json", ai_gateway)
write_matrix("screen_inventory.md", "Screen Inventory", "Exact paths were compared with `frontend/src/routes.ts`; similarly named routes were not accepted as substitutes.", screens, list(screens[0].keys()))
dump_json("screen_inventory.json", screens)
write_matrix("api_contract_matrix.md", "API and Contract Matrix", "Registered routers were traced to role dependencies, persistence, and frontend clients.", api_matrix, list(api_matrix[0].keys()))
dump_json("api_contract_matrix.json", api_matrix)
write_matrix("configuration_matrix.md", "Configuration Matrix", "Configuration presence does not count as executable behavior.", configuration_matrix, list(configuration_matrix[0].keys()))
dump_json("configuration_matrix.json", configuration_matrix)
write_matrix("data_model_matrix.md", "Data Model Matrix", "Collections/tables were traced to repositories, indexes and migrations.", data_models, list(data_models[0].keys()))
dump_json("data_model_matrix.json", data_models)
write_matrix("documentation_coverage_matrix.md", "Documentation Coverage Matrix", "Documentation was checked as supporting evidence only.", docs_matrix, list(docs_matrix[0].keys()))
dump_json("documentation_coverage_matrix.json", docs_matrix)
write_matrix("test_and_runtime_matrix.md", "Test and Runtime Matrix", "Commands, environments, exit codes and blockers from this source state.", tests, list(tests[0].keys()))
dump_json("test_and_runtime_matrix.json", tests)
master_columns = ["Feature ID", "Domain", "Feature", "Required behavior", "Implementation classification", "Source evidence", "Runtime evidence", "Screen", "API", "Agent", "Workflow stage", "Configuration", "Persistence", "Test", "Documentation", "Security control", "Known gap", "Priority", "Recommended action"]
write_matrix("master_feature_matrix.md", "Master Feature Matrix", "This is the controlling feature list and count source for the final verdict.", features, master_columns)
dump_json("master_feature_matrix.json", features)
write_matrix("gap_register.md", "Gap and Risk Register", "No calendar durations are estimated; order expresses dependency-aware remediation sequence.", gaps, list(gaps[0].keys()))
dump_json("gap_register.json", gaps)

counts = Counter(f["Classification"] for f in features)
screen_counts = Counter(s["Classification"] for s in screens)
test_counts = Counter(t["Result"] for t in tests)
summary = {
    "commit": COMMIT,
    "finalClassification": "SOURCE_INCOMPLETE",
    "featureCounts": dict(counts),
    "screensVerified": screen_counts[VERIFIED],
    "screensSourceImplementedRuntimeUnverified": screen_counts[SOURCE],
    "screensIncomplete": len(screens) - screen_counts[VERIFIED],
    "testsPassed": test_counts["PASSED"],
    "testsFailed": test_counts["FAILED"],
    "testsBlocked": test_counts["BLOCKED"],
    "topBlockers": [
        "Only two of six return scenarios have any E2E runner path; four are missing/dedicated-path incomplete.",
        "11 of 23 mandatory exact frontend routes are absent.",
        "No live OMC, parcel carrier, freight/TMS, or LSI integration proof.",
        "AI rate limits/circuits are process-local and required metrics fields/UI are incomplete.",
        "Backend/orchestrator cannot start because configuration paths resolve outside /app/config; concurrent Mongo index migration also races.",
        "Data Console generic SQL write boundary is not physically isolated from production OMC configuration.",
    ],
    "qualityEvidence": {"backendTests": 987, "frontendTests": 39, "stage4nValidatorChecks": 9, "stage4nFocusedTests": 5},
}
(OUT / "baseline_validation_summary.json").write_text(
    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
)

answers_text = "\n\n".join(f"### {question}\n\n{answer}" for question, answer in mandatory_answers)
top_gaps = "\n".join(f"{i}. {gap}" for i, gap in enumerate(summary["topBlockers"], start=1))
report = f"""# Stage 4O Complete Audit Report

## Executive verdict

**Final classification: `SOURCE_INCOMPLETE`.**

The repository has meaningful, well-tested source: all five bounded agents, strict contracts, a production-v2 Temporal event state machine, internal Returns Support services, branch safety enforcement, four bounded simulators, central AI routing/safety, persistence indexes, and role-protected APIs. Linux backend gates passed (987 tests), frontend static/unit/build gates passed (39 tests), and the Stage 4N dependency-light simulator-AI E2E passed.

It is not `SIMULATOR_VALIDATED`, `LIVE_STACK_VALIDATED`, or production-ready. Four of six return paths lack dedicated runnable scenarios, 11 of 23 mandatory exact screens are absent, the OMC and parcel simulators miss required operations/states, live dependency integrations are unproven, AI circuit/rate state is process-local, and full-stack/browser/restart gates did not all pass in this source state.

## Source state and environment

- Repository: `{DISPLAY_ROOT}`
- Git commit: `{COMMIT}`
- Audit date/time zone: 2026-07-26, Asia/Calcutta
- Initial working tree: clean
- Linux execution: Docker Desktop Linux engine; no general-purpose WSL distribution was installed.
- Python gate runtime: Linux Python 3.13.14.
- Frontend gate runtime: host Node 24.14.0/npm 11.9.0 because no general Linux host shell was available.
- Existing evidence was not used as implementation proof and was not intentionally rewritten.

## Completeness by classification

| Classification | Count |
|---|---:|
| VERIFIED_IMPLEMENTED | {counts[VERIFIED]} |
| SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | {counts[SOURCE]} |
| PARTIAL | {counts[PARTIAL]} |
| SIMULATED | {counts[SIMULATED]} |
| MOCKED | {counts[MOCKED]} |
| CONFIG_ONLY | {counts[CONFIG]} |
| DOCUMENTATION_ONLY | {counts[DOC]} |
| MISSING | {counts[MISSING]} |
| UNSAFE | {counts[UNSAFE]} |

## Screen, agent, workflow, simulator and AI verdicts

- Screens: 12 mandatory exact routes exist, but none received complete real-stack browser proof; 11 required routes are absent. Loading/empty/error states are common; explicit partial-data, permission-denied and retry states are generally missing.
- Agents: all five source implementations are typed, advisory and tested. The Fulfillment Agent lacks required UI/live authoritative proof.
- Workflow: production v2 durably waits for idempotent updates but has no scenario-specific activities, retry policies, timers/SLA timers, cancellation handler or out-of-order buffer.
- Simulators: Freight and LSI meet the listed operation sets. OMC lacks `SET_RETURN_METHOD`; Parcel lacks distinct `PACKAGE_READY` and `CARRIER_ACCEPTED`.
- AI Gateway: list expansion, tier isolation, key/model/provider rotation, bounded retry/deadline, prompt-injection checks and exact schema tests pass. Route counters/circuits are process-local; task/session/user limits and complete required metrics/UI are absent.
- Documentation: broad architecture/runbooks exist, but screen and probe claims conflict with source.

## Test and runtime results

- Passed: Python compile; Ruff lint/format; strict MyPy; 987 backend tests; frontend lint/typecheck/39 tests/build; Stage 4N AI simulator E2E (9 checks and 5 focused tests).
- Infrastructure: initial Compose wait failed during volume recovery. Core dependencies later reported healthy, but retry still exited 1 because a successful one-shot init container was treated as exited.
- `scripts/infra.sh probe`: impossible because that action is not implemented.
- Business/full-stack/browser/restart proof: blocked/not run to completion; this prevents live-stack classification.

## Security findings

1. `AIGatewayService` has no authoritative tool binding and blocks tested injection/action patterns.
2. Simulation is rejected in production by settings, API and service guards.
3. Source Mongo writes are environment-gated seed functions, but a database-level read-only credential boundary was not proven.
4. AI rate/circuit/concurrency state is process-local.
5. Data Console AI Studio can apply generic SQL INSERT/UPDATE through the configured SQL connection. A catalog allowlist exists, but there is no physically separate connection guard proving it cannot target production OMC; classified `UNSAFE`.

## Production blockers and remediation order

{top_gaps}

## Mandatory questions

{answers_text}

## Final classification

`SOURCE_INCOMPLETE` is the highest honest classification. Passing static/unit gates does not overcome missing required source paths/screens or absent same-state live-stack evidence.

## Required completion footer

Verified feature count: {counts[VERIFIED]}
Source-only feature count: {counts[SOURCE]}
Partial feature count: {counts[PARTIAL]}
Simulated feature count: {counts[SIMULATED]}
Mocked feature count: {counts[MOCKED]}
Configuration-only count: {counts[CONFIG]}
Documentation-only count: {counts[DOC]}
Missing feature count: {counts[MISSING]}
Unsafe feature count: {counts[UNSAFE]}
Screens verified: {screen_counts[VERIFIED]}
Screens incomplete: {len(screens) - screen_counts[VERIFIED]}
Tests passed: {test_counts["PASSED"]}
Tests failed: {test_counts["FAILED"]}
Tests blocked: {test_counts["BLOCKED"]}
Final classification: SOURCE_INCOMPLETE
Top blockers: six-path coverage; 11 missing mandatory screens; live integrations absent; process-local AI controls; incomplete full-stack/browser/restart evidence; unsafe generic SQL boundary.
"""
(OUT / "STAGE_4O_COMPLETE_AUDIT_REPORT.md").write_text(report, encoding="utf-8")

required_stems = [
    "repository_inventory",
    "business_logic_matrix",
    "agent_matrix",
    "workflow_state_matrix",
    "dependency_simulator_matrix",
    "ai_gateway_matrix",
    "screen_inventory",
    "api_contract_matrix",
    "configuration_matrix",
    "data_model_matrix",
    "documentation_coverage_matrix",
    "test_and_runtime_matrix",
    "master_feature_matrix",
    "gap_register",
]
missing_files = [
    str(OUT / f"{stem}.{extension}")
    for stem in required_stems
    for extension in ("md", "json")
    if not (OUT / f"{stem}.{extension}").exists()
]
for json_file in OUT.glob("*.json"):
    json.loads(json_file.read_text(encoding="utf-8"))
assert not missing_files
assert sum(1 for number in range(1, 37) if f"### {number}." in report) == 36
assert all(
    text in report
    for text in ("Verified feature count:", "Final classification:", "Top blockers:")
)

print(
    json.dumps(
        {
            "features": len(features),
            "counts": dict(counts),
            "screens": len(screens),
            "gaps": len(gaps),
            "jsonFilesValidated": len(list(OUT.glob("*.json"))),
            "markdownFiles": len(list(OUT.glob("*.md"))),
            "mandatoryAnswers": 36,
            "missingRequiredFiles": missing_files,
        },
        indent=2,
    )
)
