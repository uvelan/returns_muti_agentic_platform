# Order Discovery

**Current as of 2026-08-14, commit `dcbb7dc`.**

Order Discovery takes an associate from a partial, possibly misspelled
description of a purchase to exactly one confirmed order line, and hands that
line to the return case flow.

## What the associate can give it

Whatever the deployment configures. The catalogue is
`discovery.identification_fields` — order number, customer id, tracking number,
SKU, phone, email, customer name, product description, colour, ZIP, and anything
else an operator adds. None of those are special-cased in Python; see
[`identification-fields.md`](identification-fields.md).

Strong identifiers are matched exactly first. `discovery.strong_anchors` names
which field ids count as strong, so what "strong" means is also configuration.

## How search works

### The complete-corpus invariant

**Candidate limits may bound returned results. They must never bound the
searchable corpus.**

This is the single most important property of this subsystem, and it is the one
that was violated. An earlier implementation fetched an unfiltered batch of rows
and scored them with `difflib`, on the stated assumption that Neo4j had no
server-side approximate match and APOC was not installed. Both halves were wrong:
the full-text index `customer_name_search_v2` exists, is created by migration
`0013`, is verified ONLINE at bootstrap by `apply_neo4j_migrations.py`, and needs
no APOC.

The consequence of the old design was not slowness. It was that at production
scale the correct customer could fall outside an arbitrary, unordered client-side
window and be silently missed — the agent would truthfully report finding
nothing, which reads as a broken agent rather than a bounded search.

The `difflib` probe is deleted. A full-text query with a fuzzy term searches the
complete customer set server-side and returns matches ranked by score.

The index name is configuration (`progressive.customer_fulltext_index`), not a
constant, so an operator can repoint it without a code change.

### Search strategies

Each identification field declares its `searches`. A field may have several, and
they compose:

- **exact** — deterministic key match, used first for strong anchors;
- **FULLTEXT** — the server-side index query. A `FULLTEXT` search cannot declare
  `narrow_with`: the index *is* the predicate, and narrowing it would reintroduce
  the bounded-corpus defect through the back door. The configuration model
  rejects that combination rather than trusting an operator to remember.

Query bounding is real but sits on the *plan*, not the corpus:
`max_graph_queries_per_turn` caps how many graph queries one agent turn issues.

### Ranking

Candidates are ranked by summed field weights in millionths —
`ranking_weight_millionths` plus `exact_match_bonus_millionths` when the match is
exact rather than partial. Millionths, not floats, for the same reason every
other weight in the configuration is: integers compare and serialize identically
everywhere.

`conflict_penalty_millionths` reduces the score of a candidate that contradicts a
supplied signal. `ambiguity_gap_millionths` decides when the top candidates are
too close to auto-select.

## Clarification

When candidates are ambiguous, **the server chooses the slot to ask about.** It
picks by `clarification_priority` and by selectivity — the field that most
efficiently splits the current candidate set.

AI may phrase the approved question. AI may interpret a free-text answer into a
structured anchor. **AI may not select a customer, an order or a line**, may not
change workflow state, may not generate database queries, and may not bypass
confirmation. An explicit identifier always beats conflicting AI output.

When AI is unavailable, intercepted, rate-limited, low-confidence or returns
something invalid, the turn falls back to bounded deterministic extraction and
the flow continues. AI failure produces a deterministic response; it does not
break the business flow.

## Conversation state

The conversation is durable and multi-turn, hosted on Temporal
(`OrderDiscoveryWorkflow`). Each turn is one activity.

A `CandidateSet` is cached for `candidate_ttl_seconds` — default **900s (15
minutes)**, range 60–3,600 — and carries the conversation version. A stale
candidate card is rejected on candidate-set id, expiry, or conversation version:
three independent checks, because a card can be stale in three different ways and
any one alone leaves a hole.

## Confirmation

`CandidateSet.validate_selection` re-binds the selection to the conversation,
principal, tenant and graph generation before anything is written. A candidate
captured in one conversation cannot be confirmed in another.

Confirmation commits the case and starts exactly one `ReturnCaseWorkflow`. It is
idempotent on `(tenant | conversation | order | line-set)`. If the workflow
cannot be started, the confirmation fails — see
[`canonical-runtime-flow.md`](canonical-runtime-flow.md) §2 and §3.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/associate-returns/conversations` | List recent discovery conversations |
| `POST` | `/api/v1/associate-returns/conversations` | Start discovery with a structured anchor |
| `POST` | `/api/v1/associate-returns/chat` | Start discovery from natural language |
| `GET` | `/api/v1/associate-returns/conversations/{conversation_id}` | Load one conversation |
| `POST` | `/api/v1/associate-returns/conversations/{conversation_id}/chat` | Continue with natural language |
| `POST` | `/api/v1/associate-returns/conversations/{conversation_id}/messages` | Submit a structured clarification anchor |
| `POST` | `/api/v1/associate-returns/conversations/{conversation_id}/confirm` | Confirm and lock the selected order line |
| `POST` | `/api/v1/associate-returns/conversations/{conversation_id}/details` | Submit return details |
| `POST` | `/api/v2/order-agent/conversations/{conversation_id}/turns` | One durable agent turn |

`GET /api/returns/{session_id}/conversation` is the canonical read-only view of a
conversation. There is no canonical *write* surface: the associate flow is
partitioned by channel from `POST /api/returns`.

`/api/v2/order-agent` is the only surviving `/api/v2` prefix. It is unrelated to
the deleted V2 platform shell and merely shared the prefix.

## Failure behaviour

| Failure | Result |
|---|---|
| All AI providers unavailable | Deterministic task response; main flow continues |
| One AI key rejected | Open that key's circuit, try another validated key |
| Model removed or inaccessible | Next validated model/provider route |
| Neo4j discovery unavailable | Approved source fallback where policy and evidence permit |
| Weak fuzzy result | Never triggers graph synchronization unless explicitly enabled |
| Stale candidate card | Rejected on candidate-set id, expiry, or conversation version |
| Duplicate message | Prior idempotent result returned rather than applied twice |

## Related

- [`identification-fields.md`](identification-fields.md) — the runtime field catalogue
- [`../optimization/order-discovery-search.md`](../optimization/order-discovery-search.md) — the search optimization and its correctness invariant
- [`../screens/returns-workspace.md`](../screens/returns-workspace.md) — the screen an associate uses
- [`ai-dispatch.md`](ai-dispatch.md) — how the phrasing and interpretation calls reach a model
