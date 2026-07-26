# Stage 4O Remediation Validation

Date: 2026-07-26

Scope: source, unit, contract, provider-catalog, and frontend build validation

Excluded by operator instruction: Windows full-stack and browser E2E; run those manually on Linux.

## Outcome

- Mandatory screens: **23/23 exact routes present**.
- Backend: **992 passed, 1 skipped**, strict MyPy clean across 172 source files, Ruff clean.
- Frontend: TypeScript clean, ESLint clean, **41 tests passed**, production Vite build passed using a clean alternate output directory.
- OpenAPI: deterministic drift gate passed and generated TypeScript contracts were refreshed.
- Gemini and NVIDIA: catalogs loaded and minimal generation probes succeeded without exposing credentials.

## Closed findings

| Finding | Remediation | Evidence |
|---|---|---|
| Container configuration paths resolved under the installed Python package | Docker and Compose explicitly set all five `/app/config` paths | `backend/Dockerfile`, `compose.yaml` |
| Concurrent event-index migration raised MongoDB code 27 | Index initialization tolerates a concurrent legacy-index removal | `OperationalRepository._ensure_event_deduplication_index`; 3 index tests |
| OMC simulator lacked `SET_RETURN_METHOD` | Added guarded, persisted, deterministic method authorization | `DependencySimulationService._omc`; simulator tests |
| Parcel conflated readiness and carrier acceptance | Added `PACKAGE_READY` then `CARRIER_ACCEPTED`; handoff signals only on carrier acceptance | simulator config/service/bridge; simulator tests |
| Four return scenarios lacked source orchestration | Added `BRANCH_LTL`, `OFFSITE_PARCEL`, `DIRECT_VENDOR`, and `NO_PHYSICAL_RETURN` contracts and orchestration | `SimulationE2ERequest`, `run_e2e`, Linux runner |
| 11 mandatory screens were absent | Added dedicated route components and real authorized API clients | `OperationalWorkspacePages.tsx`, `operations.ts`, `routes.ts` |
| Provider key/model lists did not reach Compose services | Compose now passes Google/NVIDIA key and model lists | `compose.yaml` |
| Provider readiness checked only legacy singleton settings | Readiness now evaluates resolved key/model pools | `api/dependencies.py` |
| AI Studio could reuse operational/source write boundaries | Direct apply is disabled unless separate sandbox Mongo and SQL host/user/database settings exist | `AIStudioService`; 4 boundary tests |
| OpenAPI did not contain the expanded six-scenario enum | Root OpenAPI and generated TypeScript contracts refreshed | `openapi/return-platform.openapi.json`, generated `.d.ts` |

## Mandatory screen inventory

| Route | Dedicated component/API wiring |
|---|---|
| `/associate/returns` | `AssociateReturnsPage` |
| `/operations/returns/:sessionId` | `OperationsReturnDetailPage` → production artifacts |
| `/operations/return-agents` | `ReturnAgentsPage` → agent configuration |
| `/return-support/workbench` | `ReturnSupportWorkbenchPage` → Support work items |
| `/logistics/returns` | `LogisticsReturnsPage` → operational return queue |
| `/warehouse/returns` | `WarehouseReturnsPage` → operational return queue/detail |
| `/tracking/returns` | `TrackingReturnsPage` → operational return queue/detail |
| `/system/integration-outbox` | `IntegrationOutboxPage` → persisted outbox |
| `/system/dependencies` | `DependenciesPage` |
| `/system/dependency-simulator` | `OverviewPage` |
| `/system/dependency-simulator/omc` | `OmcPage` |
| `/system/dependency-simulator/parcel` | `ParcelPage` |
| `/system/dependency-simulator/freight` | `FreightPage` |
| `/system/dependency-simulator/lsi` | `LsiPage` |
| `/system/dependency-simulator/ai-metrics` | `AiMetricsPage` |
| `/system/dependency-simulator/operations/:operationId` | `OperationDetailPage` |
| `/ai-gateway/requests` | `AIRequestsPage` |
| `/ai-gateway/routes` | `AIRoutesPage` → route health |
| `/ai-gateway/tasks` | `AITasksPage` → fixed task registry |
| `/ai-gateway/metrics` | `AIMetricsPage` → attempts and summary |
| `/ai-gateway/safety` | `AISafetyPage` → deterministic safety test |
| `/ai-gateway/simulator` | `AISimulatorPage` |
| `/ai-gateway/interceptions` | `AIInterceptionsPage` |

All new remote screens expose loading, empty/error, and explicit retry behavior. Backend role dependencies remain authoritative; frontend visibility is not treated as authorization.

## Verified provider models

The safe probe listed provider catalogs and performed one minimal generation without printing keys or response bodies.

| Provider | Tier | Working model |
|---|---|---|
| Google Gemini | LIGHTWEIGHT | `gemini-3.1-flash-lite`, `gemini-2.5-flash-lite` |
| Google Gemini | STANDARD | `gemini-3.5-flash`, `gemini-2.5-flash` |
| NVIDIA | LIGHTWEIGHT | `meta/llama-3.2-3b-instruct`, `meta/llama-3.1-8b-instruct`, `nvidia/llama-3.1-nemotron-nano-vl-8b-v1` |
| NVIDIA | STANDARD | `nvidia/nemotron-3-nano-30b-a3b`, `abacusai/dracarys-llama-3.1-70b-instruct` |

Re-run:

```bash
uv run --project backend --no-dev python scripts/probe_configured_ai_models.py
```

## Validation commands and results

```text
Ruff check/format (changed backend and probe sources): PASS
MyPy --strict src: PASS — 172 source files
Pytest full backend suite: PASS — 992 passed, 1 skipped
Frontend no-write TypeScript check: PASS
Frontend ESLint: PASS
Frontend Vitest: PASS — 14 files, 41 tests
Frontend Vite production build: PASS — 1,827 modules
OpenAPI deterministic drift: PASS
Gemini/NVIDIA catalog + minimal generation probe: PASS
```

The standard frontend `typecheck`/`build` scripts could not replace Docker-owned incremental/dist artifacts on Windows. Equivalent no-write TypeScript and clean alternate-output Vite builds passed. This is an ACL condition, not a source failure.

## Linux E2E handoff

Run on Linux after rebuilding from this exact source state:

```bash
./scripts/start_stage4m_simulation.sh
for scenario in \
  BRANCH_PARCEL OFFSITE_HEAVY BRANCH_LTL \
  OFFSITE_PARCEL DIRECT_VENDOR NO_PHYSICAL_RETURN
do
  ./scripts/run_stage4m_simulated_e2e.sh "$scenario"
done
npm --prefix frontend run test:e2e:real
```

## Boundaries still requiring external/runtime proof

- Real OMC, carrier/TMS, and LSI endpoints are not proven by local unit tests or provider model probes.
- Linux full-stack, worker-heartbeat, restart/replay, browser, and accessibility evidence remains to be produced manually.
- AI route rate/circuit state remains process-local; production-wide distributed enforcement still requires a Valkey-backed implementation.
- Source Mongo read-only enforcement requires deployment credentials/roles, not only application routing.

These limitations prevent a `LIVE_STACK_VALIDATED` or `PRODUCTION_READY` claim.
