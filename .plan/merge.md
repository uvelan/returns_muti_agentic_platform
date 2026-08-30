# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Planned order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.

**Deviations in force.** (1) User-directed ≤3 parallel agents, so slices are pipelined off the preceding slice's *candidate* head rather than its merge, with rebase instructions if review forces changes; every pipelined base has so far matched the eventually-approved head. (2) Large slices are split into phases when a later phase depends on an unbuilt interface — V1 and V2 are both split, and both S1 and V2 gained a phase 1b after review or integration exposed a gap. (3) ACC phase 1 was cut early because its scope is test-only and independent.

**File-handling note:** edit `.plan/*.md` with an editor, never a shell string-replace. Two rounds of UTF-8 mojibake came from PowerShell `-replace` on these files.

## Slice status

| Slice | Branch | Status | RV rounds | Merged at |
|---|---|---|---|---|
| T0 | (trunk) | DONE | — | 2cafe2a |
| S1 | feat/s1-model-identity | **MERGED** | 1 · PASS (6bdb5bd) | 5d58b90 |
| S1 phase 1b (`actorId`) | feat/s1-actor-id | **MERGED** | 1 · PASS (98a180e) | 132b031 |
| S2 | feat/s2-delivery-spine | **MERGED** | 3 · CR → CR → PASS (b7a78ad) | dfd3036 |
| V1 phase 1 | feat/v1-template-review | **MERGED** | 2 · CR → PASS (18f671f) | b2590ef |
| V1 phase 2 | feat/v1-phase2 | **MERGED** | 3 · CR → CR → PASS (8bcce23) | b542524 |
| V2 phase 1 | feat/v2-ingress-relay | **MERGED** | 2 · CR → PASS (02da231) | 97bca1e |
| V2 phase 1b | feat/v2-ingress-relay | **MERGED** | 1 · PASS (a51c9b4) | 95b5672 |
| V2 phase 2 (frontend) | feat/v2-frontend | IN_PROGRESS · off V1p2 candidate 594bb05 | — | — |
| V3 backend | feat/v3-resolver-clarification | **MERGED** | 2 · CR (3d8715f) → PASS (c463872) | 270c223 |
| V3 frontend | feat/v3-frontend | **MERGED** | 1 · PASS (cfcbe44) | 9952f2b |
| V3 backend phase 2 (trigger) | feat/v3-resolver-trigger | IN_PROGRESS · the resolver has no production invocation site | — | — |
| ACC-1 (harness) | feat/acc-harness | **MERGED** | 2 · CR → PASS (9cb3508) | c1c2b0f |
| ACC-2 (scenarios) | not yet cut | BLOCKED on V3 | — | — |
| RV calibration | rv-calibration/seeded-hardcoding | bait CAUGHT as blocking | 1 (d59e017) | never merges |

**All nine backend slices merged:** S1, S1b, ACC-1, V1 phase 1, S2, V2 phase 1, V2 phase 1b, V3, V1 phase 2. Trunk suite: **5121 passed, 1 failed** — the single known pre-existing `test_a_rejected_return_still_opens_no_work_item`.

Remaining: the batched integration pass (in flight), the two frontend phases (in flight), then ACC-2 and the acceptance gate.

**V1 phase 2 review notes.** Round 3 closed AMENDMENT-5's implementation. Two things worth keeping: the shared execution-liveness classifier is asserted **by identity** across both surfaces (`case_panel.classify_execution_failure is case_reviews.classify_execution_failure`) rather than by comparing outcomes, which makes divergence *unrepresentable* rather than merely detected on whichever statuses someone enumerated — RV noted the boundary is that identity catches a second *copy*, not a *wrapper*, so a future third surface must be held to "finer, never different". And the `signal_id` asymmetry the slice defended — deterministic where a repeat means *again*, random where it means *somebody else*, since a deterministic approval id would collide with the frozen CAS and hand a second actor a receipt saying they succeeded when someone else's approval went out — was judged better than RV's own advisory, which it withdrew.
*One item carried to the acceptance gate, blocking nothing:* permissive `**fact` doubles still exist in other slices' suites; worth one sweep onto `scoped_fact_double.py` now that it exists.

**V2 phase 1b review notes (PASS, zero findings).** RV verified the three historically fragile items by injection rather than reading: the route walker catches a real collision with a *different* parameter name (3 failed) and passes with the normalisation reduced to identity, so the normalisation is genuinely load-bearing and errs safe (it over-normalises the `:path` converter — false positive possible, missed collision not); removing the idempotency key's length prefixes fails 4, and **the separator test fails on its own merit** rather than via the pinned literal, varying two *adjacent* parts where one carries `|` — both conditions the three earlier attempts kept missing. RV recomputed the pinned uuid5 by hand so the test cannot pass by comparing the function with itself, and checked **AMENDMENT-4's four clauses one at a time against source** rather than trusting my wording: no such transaction exists, the order is as claimed, all three steps are genuine no-ops on repeat, and `ConcurrencyConflictError` is a `RuntimeError` so the outbox retries rather than dead-letters. It also verified the wider property I had not asked for: **170 endpoints across all mounted routers, zero collisions.**
*Four observations, none findings:* the walker covers only `return_platform.api` (widening costs ~4 lines); `relay` still defaults to `None` — the same optional-port shape the omc gap was raised to close, live risk closed by the factory and its test; a pre-existing S1 stale-value re-merge on out-of-order redelivery, not amplified here; and an uncaught `DuplicateKeyError` on a concurrent upsert race that converges correctly but reports an error where a no-op would be truthful.

## Contract amendments (all in `.plan/contracts.md` §1a)

Five so far. **Four of the five are T0 errors — things I froze that did not survive contact with implementation**, and every one was caught by someone trying to build or wire the thing rather than read about it.

| # | What | Raised by |
|---|---|---|
| 1 | `literal:` admitted as a fourth binding source (enumeration amended, implementation not excused) | RV, V1p1-1 F3 |
| 2 | `return_record:` constrained to declared projection attributes (was unconstrained `getattr`) | RV, V1p1-1 F4 |
| 3 | Ingress path moved to `/inbound-messages` — **the frozen path was already served by two live handlers** | integration agent |
| 4 | omc mirror restated as **eventually once, not atomically once** — the transaction it named does not exist | V2 phase 1b |
| 5 | Recovery: retry requires a live execution; the gate moves non-terminal reviews to `HELD_FOR_OPERATIONS` on close — **the operator's own recovery action was building a permanent trap** | V1p2 + RV V1p2-1 F2 |

## Orchestrator errors (the run's discipline applies to me too)

1. **Four frozen mechanisms failed on contact** (amendments 1–5 above, plus the V1 brief's governance-proposal write path, which that endpoint refuses for a non-agent module).
2. **A stale trunk sha in the S1 phase-1b dispatch** (`24e01b1`, 122 commits behind). The slice verified it was an ancestor and followed the words over the number; RV noted branching there would have failed unmissably rather than subtly.
4. **I named `594bb05` as the frontend base; it predates the work those slices depend on.** That sha is V1 phase 2's delta report, cut from `f4c6f7f` — before V2 phase 1b and V3 merged to trunk. **Both** frontend agents caught it independently and re-based onto `aa1f261` (V1 phase 2's trunk merge), one of them naming the commit "the named base predates the work it says is merged". Fourth error of the same family: **naming a sha without verifying what it contains.** The standing rule now covers both directions — name the branch an interface claim comes from, *and* verify a named base actually contains what the brief says is in it.
3. **I described unmerged branch state as trunk state** to V3 — claiming V2 phase 1b had removed a required keyword. It had not; that work is on a branch. V3 checked rather than trusted and was right. **Rule: name the branch an interface claim comes from.**

## Recurring failure shapes (the run's checklist, earned by injection)

Every slice has shipped at least one green-but-blind test. The shapes found so far:

- **A double that accepts anything proves nothing** — `**fact`/`**kwargs` doubles hid five dead fact writes in V1p2 and still hide the fifth (V1p2-1 F1).
- **A consumer tested against a synthetic producer** — V1p2's step-14 tests signalled the workflow by hand, so nothing proved revise/cancel/redraft had a producer; redraft was completely unreachable.
- **Nobody stands at the seam** — S2 tested its payload, V1 tested its notice, and the approval signal failed to decode silently between them.
- **Green because the inputs can't exercise the property** — the entry-id collision test passed on the separator, not the length prefixes, and was believed fixed three times; adjacency was necessary but not sufficient.
- **The instrument flattens into the answer** — V2's drain loop reset every command to one `nextAttemptAt`, so the tie-break fell through to enqueue order.
- **Two things equal by construction** — V2's artifact-gate test compared the invoker's answer with the written artifact.
- **The documented reason is not the operative one** — V3's neutralise-then-bound ordering was safe because of a *space* in the truncation joiner; swapping the documented order changed nothing.
- **A negative assertion** — "does not contain" passes for the wrong reasons; pin the whole composed output as an equality.
- **Skipped on the platform that runs it** — ACC's behavioural stop/kill pin is Windows-skipped, so a structural pin was added beside it.
- **An *injection* red for the wrong reason** — the newest shape, and the same defect wearing the reviewer's clothes. V1 phase 2's first two ordering injections used `str.index` anchors that matched a *different* endpoint and silently **deleted** the liveness block instead of reordering it; both produced plausible red (6 tests) that were nearly recorded as evidence. The tell was that the parked-review test *passed*, which is impossible if liveness runs first. **Fault injection needs its own verification: confirm the injection did what it claims, not merely that something went red.**

## Integration debt (orchestrator applies at merge)

- **V2 — DONE, all four items.** Item 3 at `42b8b60`; items 1, 2, 4 at `bceffae` / `83eb1af` / `55d3316` after V2 phase 1b merged. The first attempt correctly **stopped** on three blockers and produced AMENDMENT-3 and AMENDMENT-4. Outcomes worth keeping: the mount count was **verified by the test's own AST walk** (trunk mounted exactly 35 against a recorded 33; 35+1=36) and the two previously-undocumented routers are now *named in the comment* rather than absorbed into a number — `shipment_console_router` (`7585b38`) and `template_preview_router` (V1 step:04, `176f1d5`). **This legitimately fixed `test_main_is_composition_only`**, so trunk is now down to one known pre-existing failure. `interception=ALLOW_ALL` was passed explicitly with the reason inline rather than relying on a default (the AI-01 incident). The AMENDMENT-3 check passed **in all four JSON snapshots, not one**: `list_messages` (GET), `add_message` (POST) and `receive_support_message` (POST on `/inbound-messages`) all present — the associate endpoint the previous regeneration silently dropped is intact. Verified independently by the orchestrator.
- **V3:** mount `case_clarifications` router; wire `ResolutionInvokerPort`, `GraphSyncPort`, `GraphReadPort`, `CaseFactsPort`, and `ToolExecutor`'s `contracts` allowlist. **Once V1 phase 2 lands, V3's `clarification_answered` workflow signal handler must be written and the reminder-cadence assertion unblocked** — V3 correctly declined to invent V1's contract.
- **Batching note:** the `CaseFactProjection` fix below regenerates five pinned artifacts. **Hold it until V1 phase 2 and V3 have merged** — regenerating generated files now would create avoidable conflicts for two in-flight branches. Do it in one pass with V3's router mount and its port wiring.
- **`CaseFactProjection` drops `actorId`** (`case_projection/contract.py:154`) so the REST view shows a fact without its principal. RV ruled deferral correct — the projection already omits `turnId`, `correlationId`, and phase 1's own `record_scope`/`identity_version` — but a UI asking "who authorised this" gets "nobody". Two lines plus regeneration of five pinned artifacts.
- **OpenAPI regen is six files, not one:** `npm run contracts:generate` covers `frontend/openapi/…json` + the `.d.ts`; repo-root `scripts/check_openapi_drift.py --write` then covers `openapi/`, `backend/openapi/`, root `openapi.json` and the evidence receipt.
- **`return_configuration.py` conflicts on every slice merge** — each slice appends one config field and import. Resolution is always "keep both", verified by loading the model.
- **`test_main_is_composition_only` — RESOLVED at `bceffae`** (expectation now 36, both undocumented routers named in the comment). V1 phase 2 adds two more mounts and V3 adds one, so it will need updating again at each of those merges — with the same discipline: count via the test's own AST walk, never fit the number to the test.

## MERGE HAZARD — read before merging V2 phase 2

**Both V2's and V3's frontend branches replace the same line** — `sections: []` in `frontend/src/mocks/handlers/casePanelHandlers.ts` — and **neither composes with the other**. RV's warning: a careless merge silently drops one slice's section from `dev:mock` **while both suites stay green**. V3 merged first (`9952f2b`); the integration agent running the tripwire runbook is also touching that file and has been told to leave it composable. **When V2 phase 2 merges: compose the arrays, never take a side**, and verify both sections render in `dev:mock` before committing.

Related, already handled: V3's clarifications contract test was a **deliberate tripwire** that fires the moment its route reaches the committed OpenAPI — which the integration mount + regeneration made true. Its failure message is a three-step runbook ending "delete this test". Being executed in the same merge window so the red is not misattributed to V3's merge. The *other* tests in that file guard something a permissive mock cannot — that the client sends exactly the three keys `extra="forbid"` declares — and must survive the deletion.

## Queued follow-up dispatches

- **V3 adopts `actorId`** — `operations/return_support/clarification.py` (~172): add `actor_id=answer.actor_id`, delete `"answeredBy"` from the value, fix the stale docstring and any test reading it. *Not* `api/canonical_ai.py`'s `answeredBy` — unrelated pre-existing surface.
- **V1 phase 2 adopts `actorId`** — sent with its fix round.
- **RV will check three things on both adoptions:** `actor_id` bound explicitly in the double, an assertion on the **stored camelCase key**, and the old value-level spelling **gone** — otherwise both coexist and the migration never happened.
- **Trap:** `ScopedFactAppendPort` is `(*, record_scope, **fact)`, so `actor_id` type-checks *through* `**fact` — a double captures `fact["actor_id"]` while production stores `actorId`. S1 cannot close this (no signature stops a consumer's `**kwargs`); RV converted it into the acceptance conditions above. Advisory: S1's stated reason for leaving the Protocol alone ("narrowing isn't additive") is wrong — a keyword-only param with a default is accepted by every `**fact` implementer, and would at least put `actor_id` in the contract a consumer reads while writing the double.

## Carry-forward conditions (must be written into the named future brief)

### Into the ACC phase-2 brief
1. **Business-time scenarios must assert, not assume.** The Mon–Fri fixture's id is deliberately not `default`, which prevents *shadowing* — but a scenario that never calls `with_business_calendar` silently inherits the 24/7 dev calendar and stays green while proving nothing. Assert `calendar_applied is True` or `not …is_continuous`.
2. **Run the never-executed safety nets first.** `test_chaos_restart_smoke_real_infra.py` has never run (datastores down), and the behavioural SIGTERM pin is Windows-skipped — RV narrowed its one unproven link to whether `os.killpg(getpgid(pid), SIGTERM)` reaches the child through the session established at launch. The smoke test must be the **first** thing run when the stack comes up.
3. **Acceptance 18's ordered drain rests on V2 populating the causation chain.** Assert the chain, not just the drain.

### Into every brief composing outbound Channel B text
4. **Neutralise associate- and support-authored text.** V1 phase 1 found that binding a raw `associate_notes` fact dropped composition's neutralisation, letting a `BAY ASSIGNMENT:`-shaped line restructure the message. Composition `_safe`s exactly four values (`associate_notes` + three `contact_*`) — match that parity. V3 owns this entirely now (V2 phase 1 composes no outbound text). §9's tool-safety principle applied to message *structure*.
5. **Render support-derived values as data, never markup.** `artifact.value` reaches associate-facing text; V2 phase 1b added structural bounds (256/128), but the rendering side must escape rather than interpret.

### Retirement (post-gate, RV-gated only)
6. `return_details.additional` and `bay_handling_instructions` are `_clean`-only in **both** paths — no regression today (and `additional` has no producer at all), but once composition retires, a release populating it from associate-typed text would carry framing through.
7. Per-route pinning needs a route constraint on `StructuredOutputInvoker` (shared AI-gateway code, outside V2's ownership); `block_exhausted` currently fires after candidate-count attempts rather than genuine provider exhaustion.
