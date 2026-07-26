# Data Model Matrix

Collections/tables were traced to repositories, indexes and migrations.

| Store | Model/collection/table | Definition/repository | Indexes/idempotency | Version/timestamps/audit | Startup/migration | Tests | Classification | Gap |
|---|---|---|---|---|---|---|---|---|
| Mongo | operational_returns + return_sessions | OperationalRepository + MongoReturnSessionRepository | Unique idempotency/revision/event indexes | Both versioned/audited differently | App/worker startup | session/event tests | PARTIAL | Duplicate authoritative session models. |
| Mongo | associate_conversations/messages/discovery_snapshots/return_request_snapshots | AssociateConversationService | Conversation/message/snapshot indexes | Timestamps and snapshot digests | Lazy ensure_indexes | agent tests | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Snapshot immutability not database-enforced. |
| Mongo | operational_return_items/handling_units/pickup_sites/pickup_requests/branch_staging_records | OperationalRepository | Unique business IDs and bindings | Timestamps/events | App startup | schema tests | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Retention not defined. |
| Mongo | support_work_items/support_messages | ReturnSupportService | Unique session/idempotency/thread sequence | Optimistic version/timestamps | App startup | provider tests | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Legacy support_cases also exists. |
| Mongo | shipping_instructions/shipment_events/omc_command_records/vendor_return_links/document_artifacts | OperationalRepository | Unique external IDs and source events | Timestamps/events | App startup | schema tests | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Live integration proof absent. |
| Mongo | dependency_simulation_operations/dependency_simulation_ai_metrics | MongoSimulationRepository | Unique idempotency plus query indexes | Timestamps/history | App startup | simulation tests | SIMULATED | No retention policy. |
| Mongo | ai_gateway_traces/ai_gateway_attempt_metrics/ai_gateway_rate_limits | OperationalRepository | Trace/attempt/query/TTL indexes | Timestamps/digests | App startup | AI tests | PARTIAL | Attempt schema misses required audit fields; distributed route state absent. |
| Mongo | feedback_learning_records | FeedbackLearningService | Unique session and review indexes | Evidence digest/timestamp | Lazy ensure_indexes | feedback tests | PARTIAL | Expected learning_feedback name differs; metric scope incomplete. |
| SQL | platform.bay_configuration/reservation/assignment | SQLBusinessStateRepository; migrations 002-004 | Constraints and indexes | Created/updated/expiry fields | Forward init SQL | bay tests | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Release/expiry execution missing. |
| SQL | dbo.return_requests/items/fulfillment/tracking | SQLBusinessStateRepository; migrations 001-003 | Indexes and serializable writes | row_version/timestamps | Forward init SQL | schema tests | PARTIAL | Local duplicate of facts designated OMC-authoritative. |
