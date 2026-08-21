# 00 · Authoritative context

**Writer:** coordinator only. **Readers:** all agents.
Immutable after Wave 0 except factual path/hash corrections.

```
context_version: 1
contracts_hash: fad9b246a3788aa965585d4143e5973a812efb369871674f675e8bba32891118
baseline_commit: 47f5abd7fad4e9f0e2c890ef7e762b37e45296e6
baseline_branch: refactor/unified-return-platform
integration_branch: feat/teams-bots-windows-first
authoritative_plan: TEAMS_BOTS_PARALLEL_IMPLEMENTATION_PLAN.md (user-supplied)
```

The plan document is authoritative for **design**. This file is authoritative for
**repository facts**. Where they disagree, the decision ledger resolves it — two
such conflicts are already recorded as D-1 and D-2. Do not rescan the repository
for anything recorded here.

---

## 1. What already exists, and why this is a transport and not a subsystem

This platform has a **two-channel model**, already implemented:

- **Channel A** — the associate's Order Discovery conversation.
- **Channel B** — the Returns Support thread.

`CaseView` (`operations/models.py`) carries **both** pointers,
`channelAConversationId` and `channelBWorkItemId`, and its docstring says: *"The
two channel pointers are what make Channel B -> Channel A possible at all."*

`docs/screens/support-console.md` states the Support Console is *"the human
stand-in for Channel B **while Teams is not connected**"*. Teams is therefore a
**new transport for Channel B**. The Support Console remains the fallback and must
keep working.

`FactChannel` (`operations/models.py:152`) is `CHANNEL_A | CHANNEL_B | SYSTEM`.

## 2. Seams this work attaches to

| Purpose | Symbol | Location |
|---|---|---|
| Channel B opened (Workflow bot producer) | `ReturnCaseActivities.open_support_work_item` | `workflows/return_case_activities.py:951` |
| Message text for that post | `ReturnCaseActivities.draft_support_request` | `workflows/return_case_activities.py:912` |
| Support outcome committed (Support bot producer) | `DurableSupportEventStore.record_support_response` | `operations/support_events.py:276` |
| …called from | `submit_return_outcome` | `api/return_support.py:591` |
| The RMA-issuing endpoint | `POST /work-items/{work_item_id}/return-outcome` | `api/return_support.py:489` |
| Outbox enqueue | `OperationalRepository.enqueue_integration_command` | `operations/repository.py:1308` |
| Dispatcher registration | plain `dict[str, TopicDispatcher]` in `run()` | `workers/integration_outbox.py:69-133` |
| Dispatcher protocol | `TopicDispatcher.dispatch(OutboxCommand) -> DispatchResult` | `operations/integrations/outbox.py:48` |
| Reconciliation home | `_reconciliation_sweep` | `workers/integration_outbox.py:166` |
| Case fact append | `append_case_fact` | `operations/case_repository.py:328` |
| Channel B → Channel A | `AgentTurnContext.case_facts` | `dynamic_knowledge/order_agent/contracts.py:375` |
| Untrusted text sanitiser | `neutralize_delimiters` | `graph_schema_analyzer/application/prompt_context.py:108` |
| SQL business state writer | `SQLBusinessStateRepository` | `operations/sql_business_state.py` |

**There is no registry for dispatchers.** Adding a topic means adding a key to
that dict — nothing is discovered.

## 3. Hard constraints agents must not try to design around

1. **No push into the associate's transcript.** `AtomicConversationRepository.commit_turn`
   is the only writer, reachable only from `DynamicOrderAgentCoordinator._run_turn`,
   and the only entry point is `POST /api/v2/order-agent/conversations/{id}/turns`.
   Reads and writes are scoped by `ConversationScope(tenant_id, principal_id)`
   **inside the query filter**. The RMA reaches the associate through
   `case_facts` **on their next turn**. This is accepted, not a defect to fix.

2. **No inbound endpoint exists anywhere in the backend.** Zero webhook/callback
   routes across the API, no signature verification, and
   `_resolve_principal_provider` (`main.py:1046`) *raises* unless the environment
   is development or test. The Teams gateway is the first inbound surface and it
   lives **outside** the Python application.

3. **No label and no tracking number can be generated.** `api/cases.py` declares
   `ArtifactContentState` with exactly one member, `REFERENCE_ONLY`: no object
   store, no bytes, no provider URL. Every tracking number arrives from outside and
   is stored verbatim. The only generator is the dependency simulator, which
   refuses to run in production. **Post stored references only.**

4. **`/api/rma-tickets` is the wrong path.** It writes no case fact, sets no
   `external_reference`, and never consults `support_ticket_mode`, so a ticket
   raised there is invisible to the case, the graph and any mirror. Its dedupe is
   keyed on `session_id` alone, **not** on `idempotencyKey`.

5. **SQL and Mongo cannot commit together.** The RMA is SQL Server; the outcome,
   saga record and outbox are MongoDB. Idempotent saga with leased reconciliation;
   never claim atomicity.

6. **The Order Agent prompt path has no delimiter neutralisation.** The whole
   `AgentTurnContext` is serialised to `contextJson` verbatim by
   `RoutePoolReasoningModelGateway._invoke`. Any Teams-originated text must pass
   through `neutralize_delimiters` before it can become a case fact.

## 4. Existing outbox behaviour agents must match

- Record `_id` is `uuid5(NAMESPACE_URL, idempotency_key)`; `idempotencyKey` has a
  unique index.
- Backoff is `min(3600, 2 ** min(attempt_count, 10))`. **There is no max attempt
  count** — retryable failures retry until a dispatcher raises
  `PermanentDeliveryFailure`.
- Every HTTP dispatcher sends `Idempotency-Key` and `X-Correlation-ID`.
- A topic with no registered dispatcher fails as `ADAPTER_NOT_CONFIGURED`.
- Operator visibility is free at `GET /api/v1/integration-outbox`.
- **Known gap:** `integration-outbox-worker` writes no heartbeat and does not
  appear in `/api/v1/system/dependencies`.
- Delivery statuses are **not** what the plan's §5.5 says — see **D-1**.

## 5. Environment

Windows first, native, **no WSL for the acceptance gate**. Python backend runs
from `backend/.venv`. Node gateway is a separate process with its own pinned
runtime. Infrastructure (Mongo, SQL Server, Neo4j, Valkey, Temporal) runs in
Docker via `compose.yaml`; the app itself runs on the host.

Baseline test counts are recorded in `05-verification-ledger.md`, measured on this
baseline commit with a clean tree. **Do not quote any historical number.**
