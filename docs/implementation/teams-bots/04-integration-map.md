# 04 · Integration map

**Writer:** coordinator only. Exact producer, topic, endpoint, schema and
persistence mappings. `context_version: 1`.

---

## Flow 1 · Channel B opened → Workflow bot card

```
ReturnCaseWorkflow
  -> ReturnCaseActivities.open_support_work_item      workflows/return_case_activities.py:951
       (opens the Channel B thread once, idempotency key from the case)
  -> message text from draft_support_request          workflows/return_case_activities.py:912
  -> enqueue_integration_command                      operations/repository.py:1308
       topic:            return-support.teams.workflow.post
       aggregate_type:   case
       aggregate_id:     {case_id}
       idempotency_key:  teams:workflow:{tenant_id}:{case_id}:{work_item_id}:{event_version}
  -> IntegrationOutboxDispatcher.dispatch_once        operations/integrations/outbox.py:316
  -> TeamsWorkflowDispatcher  (Agent B, new)
  -> POST http://127.0.0.1:3979/internal/v1/workflow/messages   (HMAC, contract C2)
  -> gateway resolves conversation reference by routing key
  -> Bot Connector proactive send as the Workflow bot identity
```

`draft_support_request` already falls back to a deterministic template when no
drafter is configured — reuse it, do not write a second drafter.

## Flow 2 · Support outcome committed → Support bot card

```
POST /api/v1/return-support/work-items/{id}/return-outcome    api/return_support.py:489
  -> saga: durable operation record, PENDING_SQL               (Agent C, new)
  -> idempotent SQL RMA create-or-recover, SQL_COMMITTED       operations/sql_business_state.py
  -> ONE Mongo transaction:                                    operations/support_events.py:276
       business outcome
       + integration_outbox row (topic return-support.teams.support.post)
       + saga transition BUSINESS_COMMITTED
  -> case fact: channel=CHANNEL_B, source_system=RETURN_SUPPORT_SERVICE   (D-2)
  -> signal ReturnCaseWorkflow                                 api/return_support.py:591 path
  -> TeamsSupportDispatcher (Agent B, new)
  -> POST http://127.0.0.1:3979/internal/v1/support/messages   (HMAC, contract C2)
  -> Bot Connector proactive send as the Support bot identity

idempotency_key: teams:support:{tenant_id}:{case_id}:{support_event_id}:{event_version}
```

`record_support_response` already writes the business event **and** an outbox
command inside one `session.with_transaction(...)` (`support_events.py:346-352`).
Agent C extends that transaction; Agent C does **not** invent a second one.

## Flow 3 · Back to the associate — nothing new is built

```
case fact written above
  -> OperationalRepository.latest_case_facts(case_id)
  -> RepositoryCaseStore.case_facts                dynamic_knowledge/integration/case_store.py:71
  -> _case_facts(deps, case_id)                    dynamic_knowledge/order_agent/graph_nodes.py:445
  -> AgentTurnContext.case_facts                   order_agent/contracts.py:375
  -> visible to the model on the associate's NEXT turn
```

Proven end to end by
`backend/tests/operations/test_support_outcome_reaches_channel_a_real_infra.py`.
**No agent writes to the transcript.**

## Persistence ownership

| Store | Holds | Owner |
|---|---|---|
| SQL Server | RMA: `dbo.return_requests`, `dbo.return_record`, `dbo.return_record_item`, `dbo.return_tracking`, `dbo.return_case` | Agent C |
| Platform Mongo | case, case facts, support work items/messages, `integration_outbox`, saga operation record | Agent C (saga), Agent B (outbox rows) |
| Gateway Mongo | conversation references, inbound activity ids, HMAC nonces | Agent A |

The gateway **never** touches SQL, case facts, or the platform's business
collections. The platform **never** holds a bot credential.

## Dispatcher registration

`workers/integration_outbox.py::run()` builds a plain
`dispatchers: dict[str, TopicDispatcher]` (lines 69-133) and passes it to
`IntegrationOutboxDispatcher`. Agent B adds exactly two keys. A topic with no
dispatcher fails as `ADAPTER_NOT_CONFIGURED`, which is non-retryable — so the keys
and the topic strings must match exactly.

## Card contents (both bots)

Permitted: RMA reference, return method, return location, the requirement list for
that method from `return_policy.return_method_requirements`, the case/order
reference, and a **stable business event reference** for duplicate detection.

**Forbidden:** any label or tracking value that is not a stored reference. There is
no label generation and no tracking minting in this platform — see
`00-authoritative-context.md` §3.3.
