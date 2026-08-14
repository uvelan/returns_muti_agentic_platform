# Consolidation Baseline Inventory

Phase 0 artifact. Facts only, captured at the planning baseline — no assessment, no proof reports.

**Source branch:** `feat/v2-order-discovery-integration`
**Source commit:** `c3cdd354fdef93583c2b67da219701e76489a221`
**Consolidation branch:** `refactor/unified-return-platform`
**Remote at branch creation:** `origin/feat/v2-order-discovery-integration` @ `c3cdd354fdef93583c2b67da219701e76489a221` (matched the planning baseline exactly — no rebase needed)

## Backend routers registered at startup (36)

`backend/src/return_platform/main.py:880-915`, in registration order:

```
console_router                      schema_catalog_router
operational_generation_router       ai_studio_router
graph_sync_router                   feedback_learning_router
graph_router                        graph_evidence_router
inventory_router                    sources_router
browser_router                      workspaces_router
jobs_router                         scenarios_router
audit_router                        configuration_router
copilot_operations_router           runtime_validation_router
runtime_config_router               returns_router
return_agents_router                return_support_router
production_workflow_router          physical_operations_router
return_artifacts_router             warehouse_placement_router
integration_outbox_router           associate_returns_router
dynamic_order_agent_router          data_source_config_v2_router
platform_v2_router                  support_router
ai_gateway_router                   seed_router
dependencies_router                 dependency_simulator_router
```

## Frontend routes (76 entries in `RouteDefinition[]`)

`frontend/src/routes.ts`. 40 under `/data-console/*`; remainder under `/associate`, `/operations`,
`/logistics`, `/warehouse`, `/tracking`, `/customer`, `/support`, `/return-support`, `/ai-gateway`, `/system`,
`/seed-data`, `/overview`. App-level version split in `App.tsx` (`/v1`, `/v2/config`, `/v2/copilot`).

## Agents

**Concrete (no indirection):** `agents/{bay_assignment,feedback,fulfillment,order_analysis,order_discovery,return_workflow}.py`, registered via `agents/registry.py` (`ReturnAgentRegistry.build`).

**Descriptor-only (no resolution to an implementation):** `dynamic_knowledge/agents/registry.py` (`IndependentAgentRegistry`).

## Temporal workflows and workers

`backend/src/return_platform/workflows/`: `activities.py`, `bay_assignment.py`, `eligibility.py`,
`feedback_learning.py`, `fulfillment_tracking.py`, `persistence.py`, `production_return_state.py`,
`production_return_workflow.py`, `return_request.py`, `return_workflow.py`, `stage_results.py`, `worker.py`.

`backend/src/return_platform/workers/`: `integration_outbox.py`.

Worker entrypoint scripts: `backend/scripts/run_return_workflow_worker.py`,
`run_return_orchestrator.py`, `run_outbox_publisher.py`, `run_data_job_worker.py`.

## Source connectors

`dynamic_knowledge/connectors/{mongodb,sqlserver}.py` — the mature, live-validated pair.
`data_platform/sources/mongodb/` — a second, separate MongoDB source abstraction.

## Graph connectors / writers (three separate systems, per prior investigation)

1. `dynamic_knowledge/graph/{constraints,generation,generation_writer,neo4j_writer,projector,rebuild,write_compiler}.py` — the schema-driven pipeline (canonical target).
2. `data_platform/graph/{writer,commands}.py` — the "Customer foundation graph slice" (disposition undecided, flagged for Phase 24 investigation).
3. `data_platform/graph/{schema,sync_service,synchronization,evidence_query,evidence_repository,interim_active_schema,readback,sandbox,sandbox_runner}.py` — legacy `GraphSchemaManager` + `GraphSyncService` path, still backing `data_console/api/graph_sync.py`.

## AI providers

`ai_gateway/providers/`: `anthropic.py`, `google.py`, `nvidia.py`, `ollama.py`, `openai.py`,
`openai_compatible.py`, `http.py`, `simulator.py`, `manual.py` (filesystem-polling — replaced in Phase 14),
plus `contracts.py`, `factory.py`, `schema_cleaner.py`.

## System-store collections in use today (bare literal names, pre-consolidation)

```
ai_studio_proposals            associate_conversations        associate_messages
audit                          dependency_simulation_ai_metrics
dependency_simulation_operations                              discovery_locks
discovery_snapshots            feedback_learning_records      graph_sync_runs
job_artifacts                  job_commands                   jobs
return_request_snapshots       return_session_agent_decisions
return_session_audit_events    return_session_outbox_events   return_sessions
sandbox_records                scenario_records                scenarios
worker_heartbeats              workspaces
```
None of these are resolved through a logical-name indirection today — Phase 3 (system store) and Phase 4-27
migration replace these with `system_store.collection("<logical>")`.

## Configuration domains (`backend/config/`)

```
ai_gateway.yaml            data_assets.yaml           dependency_simulation.yaml
schema_registry.yaml       data_platform/             dynamic_knowledge/
live_validation/           returns/                   seed/
v2/                        (19-module manifest, loaded at runtime, lands as DRAFT — see plan D1)
```

## Compose services (23)

```
vault  sqlserver  sqlserver-init  mongodb  mongodb-rs-init  neo4j  valkey
temporal-postgresql  temporal  temporal-ui  runtime-configuration-init  seed-runner
backend  return-workflow-worker  return-orchestrator  outbox-publisher
data-job-worker  integration-outbox-worker  frontend  platform
```
All application-tier services sit behind `profiles: ["containerized-app"]`; default `docker compose up` starts
infrastructure only.

## Bootstrap / lifecycle scripts

```
scripts/bootstrap_host.sh        scripts/bootstrap_host.ps1
scripts/run_all_host.sh          scripts/run_all_host.ps1
scripts/run_backend_host.sh      scripts/run_backend_host.ps1
scripts/run_frontend_host.sh     scripts/run_frontend_host.ps1
scripts/run_worker_host.sh       scripts/run_worker_host.ps1
scripts/prepare_runtime_configuration.sh
scripts/apply_neo4j_migrations.py
scripts/start_stage4m_simulation.sh   (stage-era, disposition: Phase 27)
```

## SQL migrations

`infra/sqlserver/init/`: `001_return_business_state.sql`, `002_domain_models.sql` (both applied by
`sqlserver-init`), `003_production_return_platform.sql`, `004_production_bay_constraints.sql` (**neither
currently applied by anything** — Phase 4 defect).

## Neo4j migrations

`data_platform/graph/migrations/`: `0010_configuration_constraints.cypher` through
`0014_configuration_release_metadata.cypher`, applied by `scripts/apply_neo4j_migrations.py`.

## Baseline test status

Not recorded (optional per D7/§5 — the working tree at this commit is the known state this plan was authored
against; no suite run performed for this baseline per the gate policy in §5 of the implementation plan).
