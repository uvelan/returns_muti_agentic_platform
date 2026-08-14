# API contract documentation

**Current as of 2026-08-14, commit `dcbb7dc`. 136 paths in `openapi.json`.**

`openapi.json` carries FastAPI docstrings of mixed depth. What it does **not**
carry — and what a caller integrating against this platform needs — is, per path:
caller role, idempotency key and semantics, concurrency control, side effects,
configuration-release behaviour, audit effects, and error taxonomy.

That is what this document adds. It is organised by **surface**, because those
seven dimensions are properties of a surface rather than of an individual path, and
136 near-identical tables would obscure the handful of places where a surface's
behaviour genuinely differs. Where a path departs from its surface's defaults, it
is called out by name.

## Envelope

Every response is envelope-wrapped: `{ data, meta }`, with `meta.request_id`
carrying the correlation id. `frontend/src/api/client.ts` **enforces** the envelope
rather than trusting the shape, and
`frontend/src/api/noVersionedPaths.test.ts` asserts that the shell reads only the
canonical versionless surface.

Send `X-Correlation-ID` on every request. It is echoed in `meta.request_id`, and it
is the only thing that makes a support conversation about a failed request
actionable.

## Error taxonomy

| Status | Meaning | Retry? |
|---|---|---|
| `400` | Malformed request | No |
| `401` | No principal | No |
| `403` | Principal lacks the capability | No |
| `404` | Not found, or not visible to this tenant/principal | No |
| `409` | **Concurrency or lifecycle conflict** — stale head revision, illegal state transition, a seed mutation already running | Re-read, then re-decide. Not a blind retry |
| `422` | Validation failure, naming the field | No |
| `429` | Rate limited | Yes, with backoff |
| `502` | **A downstream write partially landed.** The one instance is `ShipmentStateSyncFailed`: the SQL row committed, the graph projection did not | Resubmit the identical request — it is idempotent and answers `DUPLICATE` |
| `503` | Dependency unavailable | Yes |

**`409` is not a retry code.** It means the caller's assumption about current state
was wrong. Retrying the same request re-asserts the same wrong assumption.

**All three shipment verdicts are `200`.** `APPLIED`, `DUPLICATE` and `STALE` are
correct outcomes of a well-formed request, not client errors. A caller replaying a
carrier feed must be able to tell "already knew that" from "your request was
wrong", and collapsing `DUPLICATE` into `4xx` destroys that distinction.

## Capabilities

Capabilities, not roles, are the authorization unit.

| Capability | Grants |
|---|---|
| `returns.session.read` | Read return sessions, cases, conversations |
| `returns.session.write` | Create and advance sessions |
| `returns.support.act` | Act as Support: reply, issue RMAs |
| `returns.logistics.act` | Record carrier events and physical handoffs |
| `returns.warehouse.act` | Bay placement actions |
| `returns.audit.read` | Read return audit trails |
| `config.runtime.read` | Read active runtime configuration |
| `config.release.read` | Read releases |
| `config.release.promote` | **Activate a release** |
| `config.source.read` | Read source configuration and health |
| `config.source.write` | Change source configuration |
| `config.source.rebind` | Rebind a dataset |
| `graph_schema.draft.read` | Read analyses and drafts |
| `graph_schema.draft.write` | Mutate, validate, publish drafts |
| `graph_schema.generation.activate` | **Activate a schema release** — may rebuild the graph |
| `governance.proposal.read` | Read the approvals queue |
| `governance.proposal.write` | Raise proposals |
| `governance.proposal.approve` | Approve or reject |
| `governance.proposal.activate` | **Apply** an approved proposal |
| `ai.request.read` | Read the AI request log |
| `ai.interception.read` | Read held requests |
| `ai.interception.act` | **Allow, answer or cancel** a held request |
| `ai.metrics.read` | Read AI metrics |
| `ai.replay.read` | Replay and compare |
| `ai.route.write` | Change routes, providers, models |

`GET /api/principal` reports the caller's set. **Frontend hiding is presentation
only** — the backend refuses regardless, and a screen appearing is not an
authorization decision.

Read and act are separate capabilities throughout, which is what makes a read-only
operator role possible.

---

## Returns and cases — `/api/returns`, `/api/cases`, `/api/return-history`

| | |
|---|---|
| **Caller role** | `returns.session.read` to read; `returns.session.write` to write |
| **Idempotency** | `POST /api/returns` is keyed on the submitted identity. `POST /api/returns/{id}/events` is keyed on the event id — a duplicate returns the prior result rather than applying it twice |
| **Concurrency** | Conversation version on session writes. A stale version is `409` |
| **Side effects** | Creating a session starts the session-scoped flow. Events advance it |
| **Release behaviour** | **Case-pinned.** A session or case keeps the release, head revision, checksum and source it was created under. A configuration change applies to new work |
| **Audit** | Session history and the case fact log. The fact log is the **case-scoped** audit trail — distinct in scope from `/api/config/audit`, and deliberately not merged with it |
| **Errors** | `403` capability, `404` not visible to this tenant/principal, `409` stale version, `422` payload |

`GET /api/cases/{case_id}` returns the full `Case → N return records → N items`
hierarchy. Label, tracking and return location are **per record** and never on the
case — a case-level "tracking number" would be a lie the moment a second RMA
existed.

## Order Discovery — `/api/v1/associate-returns/*`, `/api/v2/order-agent/*`

| | |
|---|---|
| **Caller role** | `returns.session.read` / `returns.session.write` |
| **Idempotency** | **`confirm` is idempotent on `(tenant \| conversation \| order \| line-set)`.** A repeated *or simultaneous* confirmation returns the existing case. Turns are idempotent on message id |
| **Concurrency** | Conversation version. A candidate selection additionally validates candidate-set id, expiry and conversation version — three checks, because a card can be stale in three ways |
| **Side effects** | A turn reads the graph and may make one AI call. **`confirm` commits a case and starts exactly one `ReturnCaseWorkflow`** |
| **Release behaviour** | Conversation-pinned |
| **Audit** | Every turn records correlation id, release, head revision, checksum. Every AI call is recorded by `FinalDispatcher` |
| **Errors** | `409` stale conversation version or stale candidate set; `422` invalid anchor; `503` when the case store or workflow launcher is unavailable **in this process** |

**`confirm` fails rather than half-succeeding.** If the workflow cannot be started
the confirmation is rejected as retryable and the case is left committed, so the
next attempt resolves to the same case and starts the same workflow id.
`ORDER_AGENT_CASE_WORKFLOW_UNAVAILABLE` is raised **before** the case is written, so
a process that cannot start workflows never commits a case it cannot make reachable.

`/api/v2/order-agent` is the only surviving `/api/v2` prefix. It is unrelated to the
deleted V2 platform shell and merely shared it.

## Shipments — `POST /api/return-shipments/{return_reference}/updates`

| | |
|---|---|
| **Caller role** | `returns.logistics.act` — a carrier event is a logistics act, the same grant behind confirming a carrier booking |
| **Idempotency** | RMA + `statusAt`, decided **inside the UPDATE's `WHERE` under `UPDLOCK, HOLDLOCK`** — not by a read-then-write the route could lose a race on |
| **Concurrency** | `statusAt` is the sole ordering authority. `APPLIED` vs `STALE` is decided against it and nothing else. An unzoned `statusAt` is **rejected**, not assumed UTC |
| **Side effects** | SQL write → `APPLIED`-only targeted graph sync under an RMA-scoped generation lease → fulfilment read → two case facts → the associate's next turn |
| **Release behaviour** | Not release-pinned. The active shipment policy applies |
| **Audit** | Two case facts on `APPLIED`, none on `DUPLICATE`/`STALE`. `evidenceReference` is the same string the case fact carries, so what the associate sees and what the submitter is told cannot disagree |
| **Errors** | `200` for all three verdicts; `422` payload (lengths are the destination columns' own widths); **`502` `ShipmentStateSyncFailed`** — the row committed, the projection did not, resubmit |

A **graph outage does not fail the update** (the reading degrades to
`UNAVAILABLE` — the authoritative row is the point). A **graph sync failure does
fail the response**, because a shipment the platform accepted and the graph has
never heard of reads as `AWAITING_HANDOFF` to every agent.

## Configuration — `/api/config/*`, `/api/runtime-config`, `/api/agents`

| | |
|---|---|
| **Caller role** | `config.runtime.read` / `config.release.read` to read; **`config.release.promote`** to activate |
| **Idempotency** | Promoting an already-active release is a no-op |
| **Concurrency** | **`expected_version` / expected head revision is mandatory on promote.** Two administrators editing from the same starting point cannot both activate; the second gets `409` naming the current head revision |
| **Side effects** | Promotion causes **every process** to reconcile. The AI route pool is rebuilt at the same activation boundary |
| **Release behaviour** | This *is* the release surface. New work uses the new release; existing cases continue on their pinned one |
| **Audit** | Every administrative action: principal, timestamp, before/after, resulting checksum. At `/api/config/audit` |
| **Errors** | `409` stale head revision; `422` validation, per field path; **refusal on checksum mismatch** — a security control, not a transient error |

`GET /api/config/runtime` answers "what is **this process** serving".
`GET /api/config/adoption` answers "has **every required class** adopted". They are
different questions and are never conflated: `ACTIVATED != LIVE`.

## Graph schema — `/api/graph-schema/*`, `/api/schema-releases/*`

| | |
|---|---|
| **Caller role** | `graph_schema.draft.read` / `.write`; **`graph_schema.generation.activate`** to activate |
| **Idempotency** | Draft mutations create revisions rather than overwriting. Publishing twice is refused, not duplicated |
| **Concurrency** | Draft revision sequence. A mutation against a stale revision is `409` |
| **Side effects** | An analysis performs bounded, **masked**, read-only source reads. **Activation runs the migration strategy and may rebuild the graph into a new generation** |
| **Release behaviour** | Published schema releases are immutable |
| **Audit** | Through `audit_port`. Approvals also appear in `/api/proposals` — `ProposalKernel` is one inbox. Activation records the classification, its reasons, and the strategy run |
| **Errors** | `409` stale revision or illegal transition; `422` validation with per-element findings |

**Read `GET /api/schema-releases/{id}/migration-plan` before activating.** It
returns `ADDITIVE` / `COMPATIBLE` / `DESTRUCTIVE` **with reasons** — "rebuild"
without "why" is not reviewable. Activation used to be a pointer flip in the dark.

## Graph sync — `/api/graph-sync/runs`

| | |
|---|---|
| **Caller role** | `config.source.read` to read; write authorization to trigger |
| **Idempotency** | Not idempotent — each `POST` is a new run. Projection writes merge, so re-running is *safe*, not deduplicated |
| **Concurrency** | Fenced by generation token. A superseded writer cannot advance a watermark (`$lte` refusal) or write to a retired generation (exact-match marker check) |
| **Side effects** | Reads sources; writes a graph generation. `FULL` + non-incremental performs a **generation cutover** |
| **Release behaviour** | Projects through the active schema release |
| **Audit** | Full run record: requester, mode, record scope, per-source counts, watermarks before/after, skipped sources with reasons, generation id, fencing token, outcome |
| **Errors** | `403` write authorization; `422` invalid mode/scope combination |

A failed run marks the candidate generation `FAILED` and **the previous generation
keeps serving**. Failure degrades freshness, never availability.

## Approvals — `/api/proposals/*`

| | |
|---|---|
| **Caller role** | `governance.proposal.read`; `.approve` to decide; **`.activate`** to apply |
| **Idempotency** | A decided proposal refuses a second decision with `409` |
| **Concurrency** | The kernel owns the lifecycle. The frontend mirrors `DECISIONS_BY_STATUS` so an operator is not offered a button that returns `409`, and the kernel's refusal is surfaced verbatim when it happens anyway |
| **Side effects** | Approve records a decision. **Activate applies the change** — for a schema proposal, possibly a graph migration |
| **Release behaviour** | Activating a configuration proposal produces a release, subject to head-revision checking |
| **Audit** | Principal, timestamp, before/after. Activation records what the applied change did |
| **Errors** | `409` illegal transition for the current status, or a governance **forbidden key** — the refusal names the key |

Approve and activate are deliberately separate: approve during review hours,
activate during a maintenance window.

## AI — `/api/ai/*`

| | |
|---|---|
| **Caller role** | `ai.request.read`, `ai.interception.read`, **`ai.interception.act`**, `ai.metrics.read`, `ai.replay.read`, `ai.route.write` |
| **Idempotency** | A decided interception refuses a second decision. **Replay is not idempotent — it is a new provider call and costs money** |
| **Concurrency** | Per-key concurrency controls, rate limits and circuit state on every route |
| **Side effects** | `allow` dispatches to the provider (tokens, cost). `answer` substitutes a human, reported as **`MANUAL`**. `cancel` rejects; the caller gets its deterministic fallback |
| **Release behaviour** | Routes, prompts and limits come from the `AI_GATEWAY` domain. The route pool is rebuilt at the activation boundary |
| **Audit** | Every request: provider, model, input/cached-input/output tokens, latency, cost, outcome, correlation. Every decision: the deciding principal |
| **Errors** | `409` already decided; `429` rate limited; `503` all routes exhausted — the caller already received its deterministic fallback |

**Replay goes through `FinalDispatcher` like everything else.** A replay that
bypassed interception would be a way to launder a rejected request.

Cost for an unpriced model is reported **unknown, never `0`**. `UNKNOWN`/null
pricing semantics are preserved deliberately.

## Data sources — `/api/config/sources/*`, `/api/source-bindings/*`

| | |
|---|---|
| **Caller role** | `config.source.read`; **`config.source.rebind`** to bind |
| **Idempotency** | `PUT /api/source-bindings/{dataset}` is idempotent on the dataset |
| **Concurrency** | Last write wins on a binding, within the release |
| **Side effects** | Rebinding changes which source a dataset reads from |
| **Release behaviour** | Bindings are configuration and are hot. **Endpoint changes are restart-required and fail closed** |
| **Audit** | Administrative, at `/api/config/audit`. Activation records a receipt bound to connector type, endpoint, checksum and exact Vault secret version |
| **Errors** | `403`; `422` unknown dataset or source; `409` if the requested access mode exceeds the **code-owned connector capability** |

Graph configuration may narrow access; it **cannot broaden** it. There is no
endpoint here that makes a read-only source writable.

## Seed administration — `/api/v1/seed-data/*`

| | |
|---|---|
| **Caller role** | Administrative. **Apply, reset and delete are restricted to development and test environments** |
| **Idempotency** | Apply is keyed on the seed version and digest |
| **Concurrency** | **One seed mutation per API process at a time; concurrent requests get `409`** |
| **Side effects** | Writes source collections and syncs to the graph. `delete` removes only active seed-owned data and requires `{"confirmation": "DELETE SEED DATA"}` |
| **Release behaviour** | None |
| **Audit** | Operation records with progress and cancellation state |
| **Errors** | `409` an operation is already running; `403` in production |

## Health — `/health/live`, `/health/ready`

Unauthenticated. `live` is process liveness. `ready` includes dependency
readiness. Neither reports configuration adoption — that is
`GET /api/config/adoption`, and conflating them would make a process that is
serving the wrong release look ready.

---

## Contract drift

```bash
python scripts/check_openapi_drift.py            # verify
python scripts/check_openapi_drift.py --write    # regenerate the five artifacts
```

It regenerates the five contract artifacts and **is wired into pytest**, so a
contract change that is not regenerated fails the suite rather than shipping
silently.

The frontend has its own check:

```bash
cd frontend && npm run contracts:check
```

## Related

- [`../architecture/canonical-runtime-flow.md`](../architecture/canonical-runtime-flow.md)
- [`../architecture/security-boundaries.md`](../architecture/security-boundaries.md)
- [`../screens/README.md`](../screens/README.md)
