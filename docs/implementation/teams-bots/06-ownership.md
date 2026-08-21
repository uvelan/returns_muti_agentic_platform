# 06 · File ownership

**Writer:** coordinator only. Derived from plan §6 and decisions **D-4** and
**D-5**. `context_version: 2`.

**One writer per file. No exceptions.** If a task needs a change in a file you do
not own, raise a `BLOCKER` in your handoff and name the file — do not edit it and
do not ask another agent to merge your patch informally.

---

## Agent A — Teams gateway and Teams assets

```
services/teams-gateway/**
teams-apps/workflow/**
teams-apps/support/**
scripts/windows/*teams*        (W2-A; hand content to the coordinator if a name collides)
scripts/linux/*teams*          (W4-A)
```

Forbidden: any Python backend file, any SQL/Mongo business collection, Graph chat
posting, hand-written JWT validation.

## Agent B — Platform outbox integration and delivery

```
backend/src/return_platform/configuration/settings.py          (Python half of C3 only)
backend/src/return_platform/workers/integration_outbox.py      (whole file — D-4)
backend/src/return_platform/operations/integrations/teams*.py  (new dispatchers)
backend/src/return_platform/workflows/return_case_activities.py (workflow-opened producer — D-5)
backend/tests/operations/integrations/**                       (scoped tests)
```

Forbidden: bot credentials anywhere in Python; gateway or manifest files;
`operations/support_events.py`; RMA/saga persistence; a separate Teams
delivery-state store.

## Agent C — RMA saga, transaction and reconciliation

```
backend/src/return_platform/operations/support_events.py       (incl. the support-outcome enqueue — D-5)
backend/src/return_platform/operations/sql_business_state.py   (operation-id constraint)
backend/src/return_platform/configuration/sql_migrations/0NN_*.sql  (new migration)
saga operation repository/models                               (new module)
teams reconciliation callable                                  (new module — D-4)
backend/tests/operations/**                                    (scoped tests, excluding integrations/)
```

Forbidden: gateway or manifest files; `workers/integration_outbox.py`;
Bot Connector delivery; any claim of cross-database atomicity; making business
completion wait for Teams.

---

## Shared-but-coordinator-owned

| File | Why | Rule |
|---|---|---|
| `docs/implementation/teams-bots/0*.md` | shared memory | coordinator writes; agents read |
| `docs/implementation/teams-bots/handoffs/<agent>.md` | per-agent log | that agent appends only |
| `.env.example` | both halves of C3 | coordinator applies once at Gate W1 from both handoffs |
| `backend/config/returns/production.yaml` | not touched in this phase | see plan §5.3 — development is environment-only |

## Collision seams to watch at integration

- **`integration_outbox.py`** — B registers two dispatchers *and* wires C's
  reconciliation callable. B must take C's entry-point name from C's handoff, not
  invent one.
- **`support_events.py`** — C writes the outbox row using B's frozen topic and
  idempotency-key formats from C5. If those change, it is a coordinator decision,
  not a merge fix.
- **`settings.py`** — B adds only the four Python-side fields. Any bot credential
  appearing here is a Gate W1 failure, not a review comment.
