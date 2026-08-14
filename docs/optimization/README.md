# Optimization documentation

**Current as of 2026-08-14, commit `dcbb7dc`.**

Before this directory existed, no optimization documentation existed anywhere.
Optimizations were real and undocumented, and the one that *was* documented — the
Neo4j full-text index — carried a **false rationale** and was unused on the
canonical path. That combination is worse than silence: it told a reader the
correct search was impossible and that the wrong one was necessary.

## What every document here states

Per the audit's T-9 template:

1. the problem;
2. the scale assumption;
3. the strategy;
4. **why it is safe** — the correctness invariant it must not break;
5. indexes required;
6. caching and invalidation;
7. the consistency tradeoff;
8. the fallback;
9. the limits;
10. observability;
11. the failure mode.

Item 4 is the one that matters. An optimization without a stated correctness
invariant is a change nobody can review, and the search defect this platform
carried was exactly that shape: a bounded probe that was fast, plausible, and
silently wrong at scale.

## Documents

| Document | Covers |
|---|---|
| [`order-discovery-search.md`](order-discovery-search.md) | Indexed complete-corpus fuzzy search, candidate narrowing, query bounding, candidate-set caching, index lifecycle |
| [`incremental-sync.md`](incremental-sync.md) | Watermarks, batching, generation swap, targeted shipment sync |
| [`model-routing.md`](model-routing.md) | Tier escalation, failover, pricing, cost optimization, prompt caching |
| [`configuration-caching.md`](configuration-caching.md) | Snapshot caching, invalidation, worker adoption |
| [`connection-pooling.md`](connection-pooling.md) | The SQL Server pool |
| [`retry-and-backoff.md`](retry-and-backoff.md) | One retry policy with a permanent-vs-transient taxonomy |

## Status of every optimization the audit inventoried

Section V of the audit listed fifteen. Their current state:

| Optimization | Implemented | Documented | Notes |
|---|---|---|---|
| Indexed complete-corpus fuzzy search | ✅ **canonical path** | ✅ | The `difflib` probe is deleted; the false rationale is replaced. Was "frozen path only" with a false rationale (DOC-4) |
| Candidate narrowing | ✅ | ✅ | Completeness invariant now stated explicitly |
| Graph / full-text indexes | ✅ created, bootstrap-verified ONLINE | ✅ | Index lifecycle table in `order-discovery-search.md` |
| Query bounding (`max_graph_queries_per_turn`) | ✅ | ✅ | Bounds the *plan*, never the corpus |
| Candidate-set caching | ✅ | ✅ | `candidate_ttl_seconds`, default **900s (15 min)**, range 60–3,600 |
| Prompt caching / invariant prefix | ⚠️ provider-reported only | ✅ | Cache-read and cache-write tokens are billed separately and recorded separately. The platform does not construct an invariant prefix; the decision not to is documented in `model-routing.md` |
| Config caching + invalidation | ✅ API **and workers** | ✅ | Was API-only (CFG-01) with unreported adoption (CFG-02). All five deployed worker classes now hot-adopt, and `GET /api/config/adoption` reports it |
| Graph generation swap | ✅ **in use** | ✅ | Was built and unused (T-7). Now what `sync_service.py` runs on, with a durable monotonic fencing token |
| Incremental sync / watermarks | ✅ | ✅ | Standalone doc with operational limits |
| Batching | ✅ | ✅ | `PLATFORM_GRAPH_SYNC_BATCH_SIZE`, default **250**, range 1–5,000 |
| Retry / backoff | ✅ | ✅ | Consolidated into one policy doc with a permanent-vs-transient taxonomy |
| Connection pooling | ✅ **implemented** | ✅ | Was `pymssql.connect` per operation (PERF-02). `operations/sql_connection_pool.py`, `sqlserver_pool_max_size` default 8 |
| Model routing / cost optimization | ✅ | ✅ | Tier escalation, failover, pricing from the released catalog |
| Targeted shipment sync | ✅ | ✅ | RMA-scoped, under a generation lease. Operational SLOs in `incremental-sync.md` |
| Progressive plan fan-out | ✅ **concurrent** | ✅ | Was serial (PERF-01). Independent plans now run under `asyncio.gather`; guarding and compiling stay strictly serial so the admitted set is identical to the serial loop's |

Two entries the audit could not resolve are resolved here: prompt caching is
provider-reported and deliberately not constructed by the platform, and connection
pooling exists.

## The rule these documents exist to enforce

> **A limit may bound what is returned. It must never bound what is searched.**

Every bounding mechanism in this platform is measured against that sentence.
Candidate limits, page sizes, batch sizes and query budgets all bound *results* or
*work per unit*. None of them bounds the corpus. The one that did was a defect.
