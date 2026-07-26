# Repository Inventory

Audited root: `K:\Projects\FEG\Ret\full\returns_platform`

Commit: `c8976dab36eee87c238da5a174bfd4800bc212cc`

Initial working tree: clean.

## Toolchain and topology

- Python `>=3.13,<3.14`; Node `>=24,<25`; npm `>=11,<12`.
- 173 backend source files, 61 backend test files, 164 frontend source files, 3 Playwright specs, 58 scripts, and 140 pre-existing docs files.
- Runtime topology: MongoDB (platform/source DBs), Neo4j, SQL Server, Valkey, Temporal/PostgreSQL, FastAPI, and React/Vite/nginx.

## Packages and runtime modules

- Backend: agents, ai_gateway, api, canonical, configuration, data_console, data_governance, data_platform, dependency_simulation, operations, security, shared, workers, workflows.
- Frontend: api, components, contracts, features/operations, features/dependency-simulator, features/data-console.
- Agents: Order Discovery Agent, Return Workflow Agent, Return Fulfillment Agent, Bay Assignment Agent, Feedback Learning Agent.
- Workflows: ReturnWorkflow (legacy v1), ProductionReturnWorkflow (production v2).
- Workers: return-workflow-worker, return-orchestrator, outbox-publisher, data-job-worker.

## Duplicate/conflicting implementations

- operational_returns versus return_sessions
- operational_events versus return_session_audit/outbox events
- support_cases versus support_work_items/messages
- AI gateway attempt metrics versus dependency simulator AI metrics
- legacy ReturnWorkflow versus ProductionReturnWorkflow

## Stale/conflicting documentation

- README and Stage 4N docs list /ai-gateway/routes, /tasks and /metrics screens absent from frontend routes.
- Prompt/runbook requests scripts/infra.sh probe, but the script has no probe action.
- README uses SOURCE_VALIDATED accurately overall but screen-level completion claims conflict with source.
