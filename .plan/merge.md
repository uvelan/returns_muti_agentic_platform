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
| V2 phase 2 (frontend) | feat/v2-frontend | **MERGED** | 2 · CR → PASS (1692242e) | d1a335ca |
| V3 backend | feat/v3-resolver-clarification | **MERGED** | 2 · CR (3d8715f) → PASS (c463872) | 270c223 |
| V3 frontend | feat/v3-frontend | **MERGED** | 1 · PASS (cfcbe44) | 9952f2b |
| V3 backend phase 2 (trigger) | feat/v3-resolver-trigger | **MERGED** | 1 · PASS (23b30f04) | 35e7c0f1 |
| ACC-2 (scenarios) | feat/acc-scenarios | IN_PROGRESS | — | — |
| ACC-1 (harness) | feat/acc-harness | **MERGED** | 2 · CR → PASS (9cb3508) | c1c2b0f |
| fabrication guard (AST) | feat/fabrication-guard-ternary | **MERGED** | 1 · PASS (fb221d8a) | 85dc4271 |
| actorId fixtures | feat/actorid-required | **MERGED** — `tsc` now exits **0** on trunk | 1 · PASS (93ad88fa) | (merged) |
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
- **A guard that looks one level shallower than the reader descends.** V2's wrong-casing notice inspected the payload root and one level; the readers go two deep for `records[].artifacts[]`. A depth-2 snake payload is correctly *dropped* by the strict reader while the notice stays silent — so the card renders zero artifacts and reads as "Support has attached nothing", which is the invisibility the notice exists to prevent, one level below where it looks. **A detector must reach as far as the thing it protects.**
- **A test that proves the right thing is *asked for*, not that the wrong thing is *refused*.** V2's AMENDMENT-7 enforcement used a recording proxy to assert the exact key set each reader requested — and with the tolerant dual-read reinstated **all 169 tests stayed green**, because a tolerant reader asks the camelCase name first and finds it, so the observed sets are identical either way. Closed only by a **behavioural** guard: a wrong-cased payload must read as *nothing*, pinned as whole-value equalities (a record with every field null would pass "does not contain"). Observation cannot prove refusal.
- **A check nothing gates on.** The `actorId` optionality mismatch produced 3 `tsc` errors that survived a merge untouched, because no test script runs the typechecker. A guard that no pipeline invokes is documentation.
- **A tell that sounds decisive and isn't.** The guard branch argued a broken parse would make its AST walk see an empty tree and go *green*, so red proved the file still parsed. RV broke the syntax two ways and the finding **survived both** — `ts.createSourceFile` is error-tolerant and recovers rather than emptying. The conclusion held anyway, for a different and better reason the branch had built deliberately (`fallbackPositions` returns positions sanctioned or not, so a test asserts the walk still sees real fallbacks). **Cite the mechanism that actually fires, not the one that sounds strongest.**
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

**Both V2's and V3's frontend branches changed the base `sections: []`** in `frontend/src/mocks/handlers/casePanelHandlers.ts`, so **a conflict there is guaranteed** and neither side composes. RV's warning: taking either side silently drops that slice's section from `dev:mock` **while both suites stay green**.

**The resolution is concrete: V3's element is already there; add V2's section object as a SECOND array element.** Do not swap the array.

**⚠ There are TWO `sections: [` in that file, ~175 lines apart, and only one is the composition point.** Line **67** is `seedDraft()`'s template sections (ORDER / RETURN DETAILS — the review *draft's* own content, nothing to do with the panel section registry). The one to compose into is the second, in **`panelBody()`, now at line 242** (it read 235 before the runbook's comment addition). Resolving a conflict against the wrong one would be silent and wrong in both directions. V3's element there is `section_id: "clarifications"`. The integration agent added a paragraph to the comment block immediately above it — *one element per contributing slice, add the element, do not swap the array* — so the instruction is where whoever resolves the conflict is already looking. Verify both sections render in `dev:mock` before committing.

**Tripwire: DONE** (`266b14b8`, `92bdb479`, `aa4cbe88`). All three runbook steps executed and the tripwire deleted. Notable outcomes: step 3's regeneration produced **zero changes** — the earlier integration pass had already published everything, reported honestly rather than as an empty commit. And **the exactly-three-keys guarantee did not move and was not lost**: `src/test/schemaConformance.ts` validates **response** bodies only (`responseSchema()` reads `operation.responses`), so nothing in that machinery inspects a request — the request-side tests correctly stayed where they were. The generated request type is wrapped in `Required<>` because the two nullable fields carry `None` defaults, so the schema marks them non-required and the generator renders them optional; without it a *dropped* field would read as a deliberate `null`. `MAX_ANSWER_CHARACTERS` and the `map|reject` union stay hand-written with reasons in place — `maxLength` and `pattern` are keywords `openapi-typescript` cannot carry into a type, and keeping the union makes a misspelt choice a compile error rather than a 422 at the counter.

## CI — WIRED (`c6c15256`, `.github/workflows/checks.yml`)

The gap below is closed. The workflow runs the repository's **own** scripts and invents no command lines. Three design choices worth keeping:

- **Gate 0 is a self-test of the allowlist**, on the same reasoning as `secret-scan.yml`'s scanner self-test: both suite jobs report "acceptable" while three tests fail, and that verdict is worth nothing unless the comparator can still say no. *A broken comparator turns the whole workflow into an expensive `exit 0`.* Verified locally — its negative controls include "a suite that collapsed reports no failures, and must not read as success".
- **The suites run in full.** Nothing deselected, skipped or deleted. `scripts/ci/assert_known_failures.py` reads the JUnit report and fails on **any** unnamed failure — **and on a named failure that has started passing**, so the list is self-pruning and cannot rot into a blanket excuse. The three known failures are named with their diagnosed causes.
- **Exit codes are discriminated:** pytest/vitest `>1` means the *run* broke, not the tests, and no allowlist covers that.

**New finding it surfaced, deliberately not papered over:** `npm run check` is `lint && build`, and `build` includes `check:bundle`, which **FAILS** — all JavaScript now totals **278.5 kB gzipped against a 260.0 kB budget** (the entry chunk is fine at 79.1/80.0). The budget's own comment says to raise it "deliberately, in a commit that says what earned the weight". **Choosing a new number is a product decision, not a wiring one**, so the workflow gates `lint` and `typecheck` directly instead of through `check`, and states plainly that `vite build` and `check:bundle` are gated by **nothing** until the budget is settled. **Open decision for the user.** When settled, the `frontend-static` job collapses to a single `npm run check`.

## ⚠ The CI gap this closed (kept for the record)

The `actorId` agent found something sharper than "typecheck is not gated":

- `frontend/package.json` defines `typecheck: tsc -b --pretty false`; `build: npm run typecheck && vite build && …`; `check: npm run lint && npm run build`.
- Measured at base, `npm run typecheck` **exits 2** — so **`npm run build` and `npm run check` are red on trunk today**.
- `.github/workflows/` contains exactly one file: `secret-scan.yml`. **Nothing executes build, check, test, or the backend suite.**

**The gate exists, is red, and nobody runs it.** That is how three typecheck errors survived a merge on a run where every slice was fault-injected — and it means the slice protocol's `npm test` plus a *tolerated* `npx tsc -b` has been standing in for a `check` that would have failed. Every green suite this run has been real, but the suites were chosen by the protocol rather than by the repo's own definition of "checked". **Decision owed by the orchestrator/user, not by a slice** — no agent has been permitted to add CI config.

## ⚠ ACCEPTANCE-GATE EXPOSURE — item 10 may be unreachable as shipped

V3's backend phase 2 reports that **§9's ladder is implemented but only its first rung (case facts) is reachable in this deployment** — visibly so, by three agreeing signals: `[]` in released config, absent from `compiled_rungs`, absent from the topology. The graph, trusted-entity, tool and authorization ports were **deliberately not wired**, each with a stated reason (no question-independent case-scoped read exists; nothing maps fact names to entity names, and that mapping decides what fills a tool argument; wiring `principal_id` would invent a credential path).

Its design principle is right — *a `GraphReadPort` returning `{}` is worse than one that raises, because the model still answers confidently under the platform's name* — and an unserviceable rung being **absent** rather than stubbed is the honest posture.

**But acceptance item 10** — "Support asks a question requiring a tool → agent resolves via the registry, credentials never surfaced" — **appears unreachable as shipped.** RV is asked to rule whether this is an honest deployment posture with the gate item deferred, or whether §9 requires the rung to be serviceable before the gate can pass. **Decision owed by me once RV reports.** ACC-2's brief must not assume item 10 is testable until this is settled.

## Escalation to the harness owner — stale bases are a provisioning defect, not vigilance

**Seven independent agents have now read the branch ref instead of the sha they were given, and all seven were right to.** Instances include a snapshot naming a commit **reachable from nothing in the repo**, and a worktree arriving checked out **100+ commits behind** on an ancestor of trunk — where branching as instructed would have silently omitted every slice merged this run.

RV's judgement: *"seven independent agents reading the ref instead of the number isn't seven lucky catches. Worth fixing at provisioning before one of them doesn't notice."*

**Why it is dangerous rather than annoying:** an ancestor base **fails silently**. The work compiles, the suites pass, and the slice is simply missing everything merged since — there is no red to notice. Contracts §3 now makes ref-verification mandatory, which is a mitigation, not a fix. **The fix belongs where worktrees are provisioned.**

## Open items surfaced but not yet dispatched

- **`actorId` optionality — the diagnosis was WRONG, and the agent falsified it before spending on regeneration.** RV's diagnosis (schema non-required → optional TS type), which I passed on unverified, is **not in the causal path**. `frontend/src/api/cases.ts:51` defines `Served<T>` as `{ [K in keyof T]-?: Served<Exclude<T[K], undefined>> }`, which forces required-and-nullable **regardless of the document**. Probe A — hand-patching the `.d.ts` to exactly what a correct regeneration would emit — left **3 errors, same three files**, the `?` intact. Probe B — one line in one fixture — cleared that file: **2 errors**. The real cause: `actorId` postdates the three helpers and is the one field not written longhand, arriving only via a `Partial<>` spread, which TypeScript types optional.
  **This also dissolved my own argument against the obvious fix.** I said patching fixtures "would leave the REST view free to omit the key" — that depended on the fixtures' optionality flowing from the schema, and it does not. `Served<T>` forbids the omission by construction. **Ruled: patch the three fixtures** — MERGED, and **`tsc -b` now exits 0 on trunk**. RV reproduced both probes itself and withdrew its own diagnosis: *"I reasoned from the generated type to the schema without reading the alias sitting between them, and stated it as a finding-grade cause."* It also supplied a cross-check the agent could not claim for itself — the three error coordinates match a baseline **RV recorded in `GUARD-1.md` on a different branch before this one existed**, so the errors fixed are provably the errors originally reported. And it tested the one ordering the agent had not: `tsc` writes `.tsbuildinfo` on success and may not on failure, so remove→red→restore could in principle short-circuit into a stale green — probed, and it genuinely re-checks.
**A2 — this reshapes the deferred work and must be settled before anyone picks it up:** `CaseFactProjection.required` is currently `['factId', 'factName']`. `actorId` is absent — **but so are nine other always-serialised fields.** The dishonesty is not `actorId`-specific; it is *why `Served<T>` exists*. Fixing one field of eleven leaves the document just as misleading at the same six-artifact regeneration cost. **Scope it deliberately before someone reduces it to a one-liner.** Also: the verification instrument must target the **generated** `.d.ts`, not a `Served<>`-derived alias — `Served` strips the `?` regardless, so an assertion against the alias passes vacuously. Same trap, new costume.
**Deferred, recorded under its real justification:** making the Python field required-and-nullable is still defensible — the published document says `actorId` may be absent while the writer guarantees it is always present, so a third-party client would type it optional and write defensive code for an impossible case — but it fixes none of the errors, its `tsc` injection is *unpassable* (any red would be red for an unrelated reason), and it costs five `CaseFactProjection(...)` sites plus six regenerated artifacts. Blast radius that size must be authorised against its actual justification. `CaseFactView` stays alone: `test_case_fact_actor.py:101` pins its required set for a stored-document reason a response DTO does not share.
- **UX-copy inconsistency across panes:** `CasePanel.tsx:198` says "This reply was empty." while `SupportReplyBody.tsx:95` says "This reply is empty. Rebuild it before sending — Support would receive nothing." Same state, two sentences, two panes. Owning slice's to settle.
- **`review.conflict`'s contrast pairing** — the same failing pair at the same tint that V2 found on its own token twin. V1's token in V1's component; registered, untouched by V2, correctly.
- **`animate-bounce` — RESOLVED by restyle** (`39fd7c0`), user-directed. The dots are now a staggered `animate-pulse`: Tailwind's bounce is a squash curve built for scroll-down arrows and disagreed with the `animate-spin` loader on the line above, **and** pulse inherits `index.css`'s `prefers-reduced-motion` rule, which freezes it outright — bounce had no such handling, so the restyle fixes an accessibility gap as well as the visual one. Never suppressed. V2 was right not to invent an ignore format for a plugin that exposes none.

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
