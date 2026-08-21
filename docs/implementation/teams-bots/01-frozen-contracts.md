# 01 · Frozen contracts

**Writer:** coordinator only. Changes require a numbered decision in
`02-decision-ledger.md`. `context_version: 1`.

These are the interfaces agents implement against. Two of them differ from the
authoritative plan because the repository disagrees with it — see **D-1** and
**D-2**. Where this file and the plan differ, **this file wins**, because it is
the one checked against the code.

---

## C1 · Gateway listeners

```
Public listener   127.0.0.1:3978      <- the ONLY port the dev tunnel forwards
  POST /api/messages/workflow
  POST /api/messages/support
  GET  /health/live
  GET  /health/ready

Internal listener 127.0.0.1:3979      <- never forwarded, proxied or published
  POST /internal/v1/workflow/messages
  POST /internal/v1/support/messages
```

Two listeners because **a dev tunnel forwards a port, not a route**. Binding the
process to localhost does not hide `/internal` if it shares the forwarded port.

Health responses carry **status only** — no credentials, tenant ids, Mongo
details, service URLs, registered endpoints or configuration values.

## C2 · HMAC request contract (internal listener)

Headers:

```
X-Service-Id      key identifier, for rotation
X-Timestamp       RFC 3339, UTC, second precision
X-Nonce
X-Signature
X-Audience
Content-Type      the real header, signed as sent
Idempotency-Key   the outbox idempotency key
```

Canonical string, joined by `\n`, in exactly this order:

```
uppercase HTTP method
percent-encoded request path, no query
X-Timestamp
X-Nonce
Idempotency-Key
X-Audience
Content-Type header value
lowercase hex SHA-256 of the exact request body bytes
```

- Secret loaded from the process environment at runtime; **never persisted, never
  logged**.
- **Constant-time** comparison.
- Short, configurable replay window.
- Nonce (or signature fingerprint) stored until expiry behind a **unique TTL
  index**; a duplicate nonce is rejected **even when the idempotency key differs**.
- Hash the **exact transmitted bytes** — do not parse and re-serialise before
  verifying.
- **Reject any query string** on internal command endpoints.
- Conversation reference is resolved from a validated **routing key**. A
  caller-supplied `service_url` or arbitrary conversation id is **rejected**.

## C3 · Process-specific configuration

**Node gateway only:**

```
TEAMS_WORKFLOW_APP_ID=
TEAMS_WORKFLOW_APP_PASSWORD=
TEAMS_SUPPORT_APP_ID=
TEAMS_SUPPORT_APP_PASSWORD=
TEAMS_ALLOWED_TENANT_ID=
TEAMS_PUBLIC_BASE_URL=https://<tunnel-host>
TEAMS_PUBLIC_PORT=3978
TEAMS_INTERNAL_PORT=3979
TEAMS_MONGO_URI=
TEAMS_HMAC_KEY_ID=
TEAMS_HMAC_SECRET=
```

**Python platform / worker only:**

```
PLATFORM_TEAMS_ENABLED=true
PLATFORM_TEAMS_GATEWAY_URL=http://127.0.0.1:3979
PLATFORM_TEAMS_HMAC_KEY_ID=
PLATFORM_TEAMS_HMAC_SECRET=
PLATFORM_TEAMS_REQUEST_TIMEOUT_SECONDS=
```

**No bot application id or password may appear** in Python settings, the Python
process environment, Mongo documents, logs, outbox payloads, committed YAML or
committed `.env` files.

## C4 · Conversation-reference persistence (gateway-owned)

```
id, bot_identity, tenant_id, conversation_id, service_url,
conversation_type, channel_id, team_id, bot_id,
installed, stale, stale_reason,
created_at, updated_at, last_activity_at, reference_version,
platform_routing_key
```

Indexes:

- unique `tenant_id + bot_identity + conversation_id`
- routing lookup `platform_routing_key + bot_identity + tenant_id`
- unique **TTL-backed** inbound activity-id collection (replay prevention)
- unique **TTL-backed** internal nonce collection (HMAC replay prevention)

Optimistic concurrency on `reference_version`. Refresh from installation,
conversation-lifecycle and received-activity events. A Connector `404` marks the
reference **stale** and blocks delivery until a new lifecycle event refreshes it.

## C5 · Outbox contracts

Topics:

```
return-support.teams.workflow.post
return-support.teams.support.post
```

Idempotency keys:

```
teams:workflow:{tenant_id}:{case_id}:{work_item_id}:{event_version}
teams:support:{tenant_id}:{case_id}:{support_event_id}:{event_version}
```

**Delivery states — the existing literals, per D-1. Do not invent new ones:**

```
PENDING | RETRY | DISPATCHING | DELIVERED | BLOCKED_EXTERNAL_DEPENDENCY | DEAD_LETTER
```

`CLAIMABLE_STATUSES = ("PENDING", "RETRY")` is used verbatim in the claim query
and in a **partial index filter**. A row written with any other "retrying" spelling
is never claimed and sits outside that index — it silently stops being delivered.

Persist delivery attempts and the **activity id returned by Bot Connector**. Every
card carries a stable business event reference for duplicate detection.

## C6 · Saga contract

Durable operation record, written **before** the SQL operation:

```
operation_id        {tenant_id}:{case_id}:{support_event_id}
tenant_id, case_id, support_event_id
request_hash
sql_rma_reference
state, resume_state
attempt_count, last_error_code
lease_owner, lease_until
created_at, updated_at, version
```

Business states — **independent of Teams delivery**:

```
PENDING_SQL -> SQL_COMMITTED -> BUSINESS_COMMITTED
PENDING_SQL | SQL_COMMITTED -> RECONCILIATION_REQUIRED -> resume_state
PENDING_SQL                 -> FAILED_PERMANENT
```

Evidence-driven recovery:

| Evidence | `resume_state` | Action |
|---|---|---|
| No SQL row for `operation_id` | `PENDING_SQL` | Retry idempotent SQL creation |
| SQL row exists, no Mongo outcome | `SQL_COMMITTED` | Reuse `sql_rma_reference`, run Mongo transaction |
| Mongo outcome exists, no outbox row | `SQL_COMMITTED` | Re-run the idempotent Mongo transaction |
| Outcome and outbox both exist | `BUSINESS_COMMITTED` | Mark committed, stop |

- SQL operation id carries a **unique constraint**.
- A repeated `operation_id` with a **different canonical `request_hash` fails as
  an idempotency conflict**.
- One Mongo transaction writes the outcome, the Teams outbox row **and** the
  `BUSINESS_COMMITTED` transition. It does not include SQL.
- Reconciliation claims work with an **expiring lease or atomic CAS**.
- **A Teams outage never moves the saga out of `BUSINESS_COMMITTED`.**

## C7 · Failure classification (Bot Connector result → outbox outcome)

| Result | Treatment | Outbox status |
|---|---|---|
| `401` | Credential/configuration failure | `BLOCKED_EXTERNAL_DEPENDENCY` |
| `403` | Installation, scope, tenant or permission failure | `BLOCKED_EXTERNAL_DEPENDENCY` |
| `404` | Mark conversation reference stale | `BLOCKED_EXTERNAL_DEPENDENCY` |
| `408`, `429` | Bounded retry, honour server delay | `RETRY` |
| `409` | Interpret the exact error code; **never retry blindly** | per code |
| `413` | Permanent payload failure | `DEAD_LETTER` |
| Other valid `400` | Permanent payload/card failure | `DEAD_LETTER` |
| `5xx` | Bounded retry | `RETRY` |
| Network timeout | Bounded retry | `RETRY` |

Raise `TransientDeliveryFailure` / `ExternalDependencyNotConfigured` /
`PermanentDeliveryFailure` from `operations/integrations/outbox.py` — the
dispatcher never sets status itself.

## C8 · Business and provenance rules

- Issue the RMA through `POST /api/v1/return-support/work-items/{id}/return-outcome`.
  **Never** `/api/rma-tickets`.
- Authoritative case fact, per **D-2** — the real `append_case_fact` parameters:

  ```
  channel       = FactChannel.CHANNEL_B
  source_system = "RETURN_SUPPORT_SERVICE"
  ```

  **`delivery_transport = MICROSOFT_TEAMS` belongs on the outbox/delivery record,
  not on the case fact.** Teams delivery status is integration metadata, never
  business provenance.
- Do **not** set `SupportWorkItemStatus.EXTERNAL_PARTY_REVIEW` because a
  notification was sent. No human review is occurring.
- The RMA reaches Channel A via `AgentTurnContext.case_facts` on the associate's
  next turn. No push injection.
- Sanitise any Teams-originated text with `neutralize_delimiters` before it can
  become a case fact.
- Both bots ignore: messages from either bot identity, duplicate activity ids,
  unsupported edits/deletes, non-allowlisted tenants, and human messages without an
  explicitly supported mention or command.
