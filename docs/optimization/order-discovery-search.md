# Order Discovery search

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The problem

An associate describes a purchase in whatever terms they have — often a
misremembered or misspelled customer name. The platform must find the right order
among a full production customer and order corpus, in one conversational turn,
fast enough that a customer is standing there while it happens.

## The scale assumption

Production-scale customer and order sets: hundreds of thousands to millions of
customers, and more orders. The seed manifest's default expansion is deliberately
of that order — 10,000 customers, 20,000 products, 1,000,000 orders, 1,000,000
shipments — because an optimization that is only ever exercised against a few
hundred rows is an optimization nobody has tested.

**This assumption is load-bearing.** The defect below was invisible at small scale
and certain at large scale, and that is precisely why the scale assumption belongs
in the document.

## The correctness invariant

> **Candidate limits may bound returned results. They must never bound the
> searchable corpus.**

Everything else here is subordinate to that sentence.

## What was wrong

An earlier implementation fetched an unfiltered batch of rows from Neo4j and
scored them client-side with `difflib`. The stated justification was:

> "Neo4j has no built-in edit-distance function (APOC not installed)."

Both halves were false. A full-text index existed, was created by migration
`0013`, was verified `ONLINE` at bootstrap by `apply_neo4j_migrations.py`, and was
already queried elsewhere in the repository. It needs no APOC.

The consequence was not slowness. It was that **the correct customer could fall
outside an arbitrary, unordered client-side window and be silently missed.** The
agent would then truthfully report finding nothing — which reads as a broken agent
rather than as a bounded search, so the failure mode was also undiagnosable.

The probe is deleted.

## The strategy

A Neo4j full-text query with a fuzzy term. The index searches the **complete**
customer set server-side and returns matches **ranked by relevance score**.

Because the index has already ranked every customer by the time any limit
applies, a `LIMIT` is a bound on *results*, not on the corpus. That is the whole
difference between this and the probe it replaced.

Two bounds sit on top of the ranked results:

| Bound | What it does |
|---|---|
| `candidate_limit` | How many ranked rows are returned. Safe, because ranking happened first. |
| `relative_score_floor` (default `0.55`) | A row is kept when its relevance is within that fraction of the best row's. So a clearly-best match is not padded out with four unrelated names just because there was room for them. |

Fuzziness is tuned rather than fixed:

| Setting | Default | Purpose |
|---|---|---|
| `max_edit_distance` | 2 | Ceiling on fuzzy distance |
| `one_edit_min_token_length` | 4 | A token must be this long before one edit is allowed |
| `two_edit_min_token_length` | 8 | And this long before two are |

The minimum-length gates exist because two edits on a four-character token match
almost anything.

## Configuration, not constants

| Key | Effect |
|---|---|
| `discovery.progressive.customer_fulltext_index` | Which index to query. **Configuration, not a constant** — an operator can repoint it without a code change. |
| `enabled` | Whether full-text search runs at all |
| `discovery.identification_fields[].searches` | Which strategies each field supports |

A `FULLTEXT` search **cannot** declare `narrow_with`. The configuration model
rejects that combination, because narrowing a full-text search reintroduces the
bounded-corpus defect through the back door: the index *is* the predicate.

## Candidate narrowing

Strong anchors match exactly first and narrow the set before fuzzy search runs.
Narrowing is safe **only because it narrows on an exact, deterministic predicate** —
an order number either matches or it does not, and a candidate excluded by an exact
anchor was genuinely excluded, not merely ranked low.

The completeness invariant therefore holds through narrowing: within the exactly
matched set, the fuzzy search is still complete.

Ranking is by summed field weights in millionths, plus
`exact_match_bonus_millionths` for an exact rather than partial match, minus
`conflict_penalty_millionths` for contradicting a supplied signal.
`ambiguity_gap_millionths` decides when the top candidates are too close to
auto-select and a clarification is required instead.

## Query bounding

`max_graph_queries_per_turn` caps how many graph queries one agent turn issues.

This bounds the **plan**, not the corpus. A turn that exhausts its budget stops
planning further searches; it does not truncate the search it did run.

Independent plans execute **concurrently** (`asyncio.gather`). Guarding and
compiling stay strictly serial, so the admitted set is the same set — and the same
prefix of it — that the serial loop would have admitted, and on failure the same
exception is raised as the serial loop would have raised, in plan order. The
concurrency changes *when* searches run, not *which*.

## Caching and invalidation

| Cache | TTL | Invalidation |
|---|---|---|
| `CandidateSet` | `candidate_ttl_seconds`, default **900s** (range 60–3,600) | Expiry; conversation version change; candidate-set id mismatch |

A stale candidate card is rejected on **three** independent checks — candidate-set
id, expiry, and conversation version — because a card can be stale in three
different ways and any one alone leaves a hole.

Pagination ("show next") pages a cached result set only when the new search has the
same **search intent signature**. That signature is computed over the
identification catalogue's keys rather than a tuple written in code, and this is
load-bearing rather than tidy: the definition of "the same signals" has to move
when an operator adds a field. It also covers any key the model supplied that the
catalogue does not recognize, because two searches differing only in an
unrecognized key are still two different searches.

## Indexes required

| Index | Created by | Verified |
|---|---|---|
| `customer_name_search_v2` (full-text) | Neo4j migration `0013` | `ONLINE` at bootstrap by `apply_neo4j_migrations.py` |
| Node key constraints and relationship indexes | Derived from `ActiveSchema` by `dynamic_knowledge/graph/constraints.py` | Applied at schema activation |

### Index lifecycle

1. A migration creates the index. Migrations are checksum-tracked; a modified
   migration file is rejected after application.
2. Bootstrap verifies it reports `ONLINE`. An index that exists but is still
   populating would return incomplete results — which is the defect this whole
   document is about — so `ONLINE` is checked rather than existence.
3. Runtime queries it by its **configured** name.
4. A schema activation may require new constraints or indexes; those are derived
   from the active schema, not hand-maintained.
5. Repointing to a differently-named index is a configuration change and needs no
   deployment.

## The consistency tradeoff

The graph is a **projection**. It is as fresh as the last sync that covered the
relevant source assets.

So discovery can miss an order created seconds ago and not yet synced. That is the
accepted tradeoff, and it is the right one: searching sources directly per turn
would put per-associate load on production source systems and would violate the
source read-only-through-the-graph design. Freshness is managed by sync
scheduling, not by bypassing the graph.

A weak fuzzy result **never** triggers graph synchronization unless explicitly
enabled — otherwise a misspelling becomes a sync trigger and an associate typing
badly becomes a load generator.

## The fallback

| Failure | Fallback |
|---|---|
| Full-text search disabled by configuration | Exact strategies only |
| Neo4j discovery unavailable | Approved source fallback, **where policy and evidence permit** |
| AI intent extraction unavailable/invalid/low-confidence/intercepted/rate-limited | Bounded deterministic extraction |
| Zero candidates | A real answer. The agent says what it searched. **Not** an error, and not a reason to widen anything silently |

There is deliberately **no** fallback to a client-side scoring probe. That was the
defect.

## The limits

- Fuzzy indexes contain **approved natural-language fields only**. A field is not
  full-text searchable merely because it is a string.
- Phone and email are matched on domain-separated HMAC evidence, so they are
  **exact-only** — an HMAC of a misspelling is unrelated to an HMAC of the correct
  value. There is no fuzzy phone search and there cannot be one.
- `max_edit_distance` is 2. A name wrong by three edits will not be found.
- Minimum token lengths mean short tokens get less fuzziness.
- Graph freshness bounds what is findable at all.

## Observability

Every turn records: which plans were built, which ran, queries used against
budget, the index queried, candidate counts before and after the score floor, and
the graph generation read. Evidence records back each candidate.

`GET /api/graph-sync/runs` is where a freshness question is answered — "the order
is not in the graph yet" and "the search missed it" are different problems and the
run history separates them.

## The failure mode

**Historically:** silent incompleteness. Fast, plausible, no error, wrong answer —
the worst failure shape a search can have.

**Now:** a search that cannot run reports that it cannot run, and a search that
runs is complete over its corpus. The remaining failure mode is honest staleness —
the graph does not yet contain a recent order — and it is visible in sync run
history rather than presenting as a bad match.

## Related

- [`../architecture/order-discovery.md`](../architecture/order-discovery.md)
- [`../architecture/identification-fields.md`](../architecture/identification-fields.md)
- [`incremental-sync.md`](incremental-sync.md)
