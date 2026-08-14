# Documentation remediation ledger

**Current as of 2026-08-14, commit `dcbb7dc`.**

Disposition of every documentation defect in Section S (DOC-1…DOC-19), every
replacement text in Section T (T-1…T-16), and the Section U and V matrices of
`UNIFIED_RETURNS_PLATFORM_COMBINED_AUTHORITATIVE_AUDIT_0615921.md`.

The audit is anchored at `0615921`. **The code has moved well past it**, so several
Section T replacement texts are now themselves wrong — they describe defects that
have since been fixed, and applying them verbatim would reintroduce a false
statement. Those are recorded as `SUPERSEDED` with the reason, not silently dropped
and not blindly applied.

Dispositions used: `FIXED` · `ALREADY_RESOLVED_AND_VERIFIED` · `SUPERSEDED` ·
`NOT_REPRODUCIBLE`.

## Section S — documentation defect inventory

| ID | Sev | Subject | Disposition | Where |
|---|---|---|---|---|
| DOC-1 | P1 | `confirm_order` docstring claimed `ReturnCaseWorkflow` "does not exist yet" | `ALREADY_RESOLVED_AND_VERIFIED` — rewritten when WF-01 landed, and it goes further than T-1 asked by documenting the recovery sweep | `dynamic_knowledge/order_agent/graph_nodes.py:1264` |
| DOC-2 | P1 | README ownership table marked SQL Server read-only while the platform writes eleven of its own tables | `FIXED` | `README.md` ownership table; [`architecture/security-boundaries.md`](architecture/security-boundaries.md) |
| DOC-3 | P1 | `ReturnCaseTimingConfiguration` asserted business-calendar semantics that nothing implemented | `ALREADY_RESOLVED_AND_VERIFIED` — SLA-01 implemented the semantics and the docstring was rewritten to describe post-fix behaviour, including the Friday-16:30 failure it fixed | `configuration/return_configuration.py:511` |
| DOC-4 | P1 | Fuzzy-probe comment justified a bounded `difflib` scan with "Neo4j has no built-in edit-distance function (APOC not installed)" | `ALREADY_RESOLVED_AND_VERIFIED` — replaced by SRCH-01 | `dynamic_knowledge/order_agent/search_strategy.py:64-75` |
| DOC-5 | P2 | README claimed "four canonical domains" | `FIXED` — **nine** are registered | `README.md`; [`screens/README.md`](screens/README.md) |
| DOC-6 | P2 | Consolidation banner named a target tree containing `business` and a top-level `graph` | `FIXED` — banner removed; the actual 20-package tree is documented | `README.md` |
| DOC-7 | P2 | `AgentDescriptor.failure_policy` comment implied enforcement | `SUPERSEDED` — see T-6 |
| DOC-8 | P2 | `_LEGACY_GENERATION_ID` / `_LEGACY_FENCING_TOKEN` carried no explanation | `ALREADY_RESOLVED_AND_VERIFIED` — see T-7 | `data_platform/graph/sync_service.py:98-135` |
| DOC-9 | P2 | No functional screen documentation existed anywhere | `FIXED` — ten documents | [`screens/`](screens/README.md) |
| DOC-10 | P2 | No optimization documentation existed | `FIXED` — six documents | [`optimization/`](optimization/README.md) |
| DOC-11 | P2 | 136 paths lacked caller role, idempotency, concurrency, side effects, release behaviour, audit effects | `FIXED` | [`api/README.md`](api/README.md) |
| DOC-12 | P2 | No per-family security classification, editability, hot-change, propagation, in-flight or rollback documentation | `FIXED` | [`configuration/families.md`](configuration/families.md) |
| DOC-13 | P3 | Interception status stale | `FIXED` — see T-12 | [`architecture/ai-dispatch.md`](architecture/ai-dispatch.md); [`screens/ai-control-center.md`](screens/ai-control-center.md) |
| DOC-14 | P3 | Bay described with older source-dependency behaviour | `FIXED` — see T-13 | [`architecture/bay-assignment.md`](architecture/bay-assignment.md) |
| DOC-15 | P3 | Frontend README was the stock Vite template | `FIXED` — replaced with a platform runbook | `frontend/README.md` |
| DOC-16 | P3 | Hot-config claims conflicted with runtime composition | `FIXED` — and the claim is now *true*, with the verification command beside it | `README.md`; [`configuration/families.md`](configuration/families.md); [`optimization/configuration-caching.md`](optimization/configuration-caching.md) |
| DOC-17 | P3 | 12+ superseded plan documents stood beside current docs as competing truth | `FIXED` — 28 documents archived with supersession named | [`archive/README.md`](archive/README.md) |
| DOC-18 | P4 | README mojibake (`â€"` for em-dash, `Â§` for section sign) | **`NOT_REPRODUCIBLE`** — see below | — |
| DOC-19 | P4 | `README-back.md`, `PACKAGE_MANIFEST.md`, `AGENTS.md` of unclear currency | `FIXED` | See below |

### DOC-18 — not reproducible, and worth recording why

`README.md` at `0615921`, the audit's own anchor commit, decodes as clean UTF-8. Its
only non-ASCII codepoints are `U+00A7` (§), `U+2014` (em dash), `U+2026` (ellipsis)
and `U+2192` (→). There is no `â`, no `Â`, and no byte sequence that would render as
`â€"`.

`â€"` **is** what a UTF-8 em dash looks like when decoded as cp1252, and `Â§` is what
a UTF-8 `§` looks like the same way. So the corruption was in the audit's reader, not
in the file. Verified again at `dcbb7dc`, and across all 66 current documentation
files: the only occurrences of those sequences anywhere are the two places
`UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md` **quotes them as examples**, which
is correct content.

Nothing was changed for this item. Recording it rather than claiming a fix, because a
ledger entry saying "fixed" against a file that was never broken is the same class of
error the ledger exists to catch.

### DOC-19 — disposition

| File | Action |
|---|---|
| `README-back.md` | **Deleted.** A stale copy of `README.md`, superseded by the README rewrite |
| `PACKAGE_MANIFEST.md` | **Archived** to `archive/stage-plans/PACKAGE_MANIFEST_STAGE_4M.md`. A Stage 4M artifact whose claims about dedicated simulator UI pages Wave F4 falsified. Date-stamping a document with false content does not make it current |
| `AGENTS.md` | **Corrected and date-stamped.** It directed every agent to push to `feat/v2-order-discovery-integration`, which has not been the working branch for this whole program, and its three task ladders named specific superseded model versions as if they were requirements. The ladders keep their shape; the model names are gone |

## Section T — replacement text

| ID | Subject | Disposition |
|---|---|---|
| T-1 | `confirm_order` docstring | `ALREADY_RESOLVED_AND_VERIFIED`. Applied when WF-01 landed. The shipped text covers everything T-1 asked and adds the recovery path: the failure is retryable, the case is left committed so the next attempt resolves to the same case, and `workflows/return_case_recovery.py` closes the gap when no next attempt arrives. **Do not re-apply** — T-1 verbatim would drop that paragraph |
| T-2 | README ownership row | `FIXED`. Applied with the note beneath the table, and the table now enumerates all eleven platform-owned objects rather than the four T-2 listed |
| T-3 | `ReturnCaseTimingConfiguration` docstring | **`SUPERSEDED`**. T-3 describes the wall-clock defect as **open** — *"business-calendar arithmetic … is tracked as an open defect"*. SLA-01 fixed it: the arithmetic runs in `resolve_business_deadline` against the configured calendar. Applying T-3 verbatim would make a true docstring false. The shipped text describes post-fix behaviour and keeps the defect as history: the 16:30-Friday case that chased at 18:30, 20:30, 22:30 and parked at 00:30 Saturday |
| T-4 | Fuzzy-probe comment | `ALREADY_RESOLVED_AND_VERIFIED`. Applied with SRCH-01, including the completeness invariant and the configurable index name |
| T-5 | README architecture section | `FIXED`, with two corrections to T-5 itself. T-5 asked for **seven** registered domains; **nine** are registered (`/support` and `/operations` were not in T-5's list). T-5 also asked the README to name Data Sources and Approvals as "required and absent" — **both now exist**, so they are documented as shipped screens |
| T-6 | `AgentDescriptor.failure_policy` comment | **`SUPERSEDED`**. Track E2 proved `failure_policy` was decorative and deleted **the field and the class**. There is nothing left to comment. What replaced it is better than the comment T-6 asked for: `AgentConfiguration` carries an explicit note that there is *deliberately* no `failure_policy`, because failure handling is different **control flow** per workflow phase rather than a value — `_gather_bay` absorbs and continues, `_open_support` falls back to the deterministic template, `_synchronize_return_records` parks the case. A configured value could not have produced any of those; it could only have contradicted them |
| T-7 | `sync_service.py` generation pinning | `ALREADY_RESOLVED_AND_VERIFIED`, and inverted by reality. T-7 documents in-place mutation and a constant fencing token as the **current** design and describes adopting generations as future work. Track G adopted them: the service now runs on the blue/green machinery, the fencing token is a durable monotonic counter, and `legacy-live` survives only as a bootstrap value. The module docstring documents the current design and keeps T-7's three consequences as the reason it changed |
| T-8 | Screen documentation template | `FIXED`. Ten documents, all sections |
| T-9 | Optimization documentation template | `FIXED`. Six documents, all eleven fields |
| T-10 | Per-endpoint API documentation | `FIXED`. Organised by surface |
| T-11 | Per-family configuration documentation | `FIXED`. With one correction: T-11 says the hot-config claim "must be corrected to say that workers are startup-bound today". Workers are **no longer startup-bound** (CFG-01), and adoption **is** reported (CFG-02). Applying T-11's correction verbatim would document a fixed defect as open. The document instead states the current behaviour and gives the command to verify it |
| T-12 | AI dispatch boundary | `FIXED`. `ai/gateway/final_dispatch.py` is the single boundary. Documented as implemented, with the three-loop history as the reason |
| T-13 | Bay Assignment | `FIXED` |
| T-14 | RMA persistence boundary | `FIXED` |
| T-15 | Shipment management | `FIXED`, with the concrete contract: `POST /api/return-shipments/{return_reference}/updates`, `APPLIED`/`DUPLICATE`/`STALE` all 200, `statusAt` as sole ordering authority, 502 on projection failure |
| T-16 | Runtime configuration adoption | `FIXED`. Documented as implemented, with the six required process classes named |

## Section U — screen documentation matrix

All ten rows were `❌ (inline only)` with "all" sections missing.

| Screen | Document | Section U's additional requirement |
|---|---|---|
| Landing | [`screens/landing.md`](screens/landing.md) | Card status model and what `NO BACKEND YET` means — documented, including why the badge was **removed** from Operations |
| Returns Workspace | [`screens/returns-workspace.md`](screens/returns-workspace.md) | RMA/tracking panels populate only via `ReturnCaseWorkflow` (WF-01), and resume/failure semantics — documented |
| Support Console | [`screens/support-console.md`](screens/support-console.md) | Signal-not-write model, the WF-01 precondition, the bay panel, the full RMA hierarchy — documented. The bay panel is **no longer absent**, and the RMA form is now repeatable |
| Case Operations | [`screens/case-operations.md`](screens/case-operations.md) | Which half is backed, timeline, agent state, permitted interventions — documented, including the three questions no API answers |
| Data Sources | [`screens/data-sources.md`](screens/data-sources.md) | Section U required an **implementation-gap entry** (UI-02). **Superseded** — the screen exists as its own domain; documented as shipped |
| Graph Schema Studio | [`screens/graph-schema-studio.md`](screens/graph-schema-studio.md) | Draft-scoped tabs vs domain sections, analyzer lifecycle, scope, cutover — documented |
| Sync Control | [`screens/sync-control.md`](screens/sync-control.md) | Mode/recordScope/watermark semantics, skipped-source reporting, manual-trigger authorization — documented |
| Configuration | [`screens/configuration.md`](screens/configuration.md) | Key/value ↔ raw JSON consistency, versions, diff, rollback, activation, adopted-release readback — documented |
| AI Control Centre | [`screens/ai-control-center.md`](screens/ai-control-center.md) | Which invocation paths can be intercepted (AI-01) — documented. The answer is now **all of them** |
| Approvals | [`screens/approvals.md`](screens/approvals.md) | Section U required an **implementation-gap entry** (UI-01). **Superseded** — the screen exists; documented as shipped |

## Section V — optimization documentation matrix

Fifteen rows re-scored against current code. Four have moved since the audit and two
of the audit's figures are wrong against it.

| Optimization | Audit | Now | Document |
|---|---|---|---|
| Indexed complete-corpus fuzzy search | ⚠️ frozen path only, rationale **false** | ✅ canonical path, rationale replaced | [`order-discovery-search.md`](optimization/order-discovery-search.md) |
| Candidate narrowing | invariant unstated | invariant stated | same |
| Graph / full-text indexes | inline migration comment | lifecycle table | same |
| Query bounding | inline | documented; bounds the plan, never the corpus | same |
| Candidate-set caching | "30-min TTL" | **`candidate_ttl_seconds`, default 900s** — the audit figure is wrong | same |
| Prompt caching | ❓ `NOT PROVABLE` | **Resolved.** Provider-reported cache reads are billed and recorded separately; the platform does not construct an invariant prefix, and the reason is recorded | [`model-routing.md`](optimization/model-routing.md) |
| Config caching + invalidation | ✅ API only (CFG-01), adoption unreported (CFG-02) | ✅ API **and** all five worker classes; adoption reported | [`configuration-caching.md`](optimization/configuration-caching.md) |
| Graph generation swap | ⚠️ built, unused | ✅ **in use**, durable monotonic fencing token | [`incremental-sync.md`](optimization/incremental-sync.md) |
| Incremental sync / watermarks | ✅ inline | standalone doc with limits | same |
| Batching | "1,000-row SQL batches, `GRAPH_SYNC_BATCH_SIZE`" | **`PLATFORM_GRAPH_SYNC_BATCH_SIZE`, default 250** — the audit figure and the variable name are both wrong | same |
| Retry / backoff | ✅ scattered inline | one policy doc with a permanent-vs-transient taxonomy | [`retry-and-backoff.md`](optimization/retry-and-backoff.md) |
| Connection pooling | ❌ `pymssql.connect` per operation (PERF-02) | ✅ **implemented** — `operations/sql_connection_pool.py` | [`connection-pooling.md`](optimization/connection-pooling.md) |
| Model routing / cost | ✅, rationale undocumented | documented | [`model-routing.md`](optimization/model-routing.md) |
| Targeted shipment sync | ✅ strong source docs | operational SLOs added | [`incremental-sync.md`](optimization/incremental-sync.md) |
| Progressive plan fan-out | ❌ serial (PERF-01) | ✅ **concurrent** under `asyncio.gather`; guarding and compiling stay serial so the admitted set is identical | [`order-discovery-search.md`](optimization/order-discovery-search.md) |

## Directive §19 J4 — required coverage

| Topic | Document |
|---|---|
| Canonical runtime flow | [`architecture/canonical-runtime-flow.md`](architecture/canonical-runtime-flow.md) |
| Order Discovery | [`architecture/order-discovery.md`](architecture/order-discovery.md) |
| Dynamic field configuration | [`architecture/identification-fields.md`](architecture/identification-fields.md) |
| Bay | [`architecture/bay-assignment.md`](architecture/bay-assignment.md) |
| Support / RMA | [`architecture/rma-and-shipment.md`](architecture/rma-and-shipment.md), [`screens/support-console.md`](screens/support-console.md) |
| Shipment / tracking | [`architecture/rma-and-shipment.md`](architecture/rma-and-shipment.md) |
| Worker hot adoption | [`architecture/configuration-adoption.md`](architecture/configuration-adoption.md) |
| AI dispatch / interception | [`architecture/ai-dispatch.md`](architecture/ai-dispatch.md) |
| Graph generations | [`architecture/graph-generations.md`](architecture/graph-generations.md) |
| Graph Analyzer | [`architecture/graph-analyzer.md`](architecture/graph-analyzer.md) |
| Data Sources | [`screens/data-sources.md`](screens/data-sources.md) |
| Approvals | [`screens/approvals.md`](screens/approvals.md) |
| Case Operations | [`screens/case-operations.md`](screens/case-operations.md) |
| Security boundaries | [`architecture/security-boundaries.md`](architecture/security-boundaries.md) |
| Source read-only policy | [`architecture/security-boundaries.md`](architecture/security-boundaries.md) |
| Startup | [`operations/startup.md`](operations/startup.md) |
| Shutdown | [`operations/shutdown.md`](operations/shutdown.md) |
| Reset | [`operations/reset.md`](operations/reset.md) |
| Recovery | [`operations/recovery.md`](operations/recovery.md) |
| Troubleshooting | [`operations/troubleshooting.md`](operations/troubleshooting.md) |

## Defects found while documenting

Documentation work reads code against claims, which surfaces claims nothing supports.
Two were found and neither is a documentation problem:

**The `jobs` worker in the Linux host launchers.** Commit `60cd6c3` deleted the
data-job-worker, but `scripts/linux/09_start_workers.sh:5` still iterates over a
`jobs` worker and `scripts/linux/11_validate_host_processes.sh:4` still checks for
`worker-jobs`. `run_worker_host.sh` has no `jobs` case and exits `2`, and advertises
`jobs` in two of its three usage strings while its own case statement rejects it. A
full `run_all_host.sh` therefore leaves a dead `worker-jobs` and fails host-process
validation. Flagged for separate repair; documented as a known issue in
[`operations/startup.md`](operations/startup.md) and
[`operations/troubleshooting.md`](operations/troubleshooting.md), and those notes come
out when it is fixed.

**No order-discovery worker is started on a host deployment.**
`09_start_workers.sh` starts four workers and `order-discovery-worker` is not among
them, while `REQUIRED_PROCESS_CLASSES` requires it for adoption. So
`GET /api/config/adoption` reports `ACTIVATING` indefinitely on a host deployment even
when everything that *is* running has adopted. Same flag; documented in the same two
places.

## Maintaining this documentation

Each document states the commit it is current as of. When one explains a mechanism it
also states what the mechanism replaced and why — a design without its rejected
alternative is a design nobody can review, and most of the defects in the audit were
introduced by a change whose reasoning was never written down.

The failure mode this ledger guards against is the one DOC-17 recorded: documents
accumulating beside each other with nothing to say which is current. When a document
here stops being true, either fix it or move it to [`archive/`](archive/README.md) and
name what superseded it. Leaving it in place is the worse of the two options that
looks like the safer one.
