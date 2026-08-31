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
| ACC-2 (scenarios) | feat/acc-scenarios | **MERGED** | 3 · CR → CR → PASS (dd583e49) | 29f033b6 |
| ACC-1 (harness) | feat/acc-harness | **MERGED** | 2 · CR → PASS (9cb3508) | c1c2b0f |
| fabrication guard (AST) | feat/fabrication-guard-ternary | **MERGED** | 1 · PASS (fb221d8a) | 85dc4271 |
| actorId fixtures | feat/actorid-required | **MERGED** — `tsc` now exits **0** on trunk | 1 · PASS (93ad88fa) | (merged) |
| ACC-3 (category B audit) | feat/acc-audit-b | **MERGED** | 3 · CR → CR → PASS (ACC3-3) | d6a08097 |
| CI backend lint | feat/ci-backend-lint | **MERGED** | 2 · CR → PASS (CI-LINT-2) | a683f648 |
| CI `.env` (backend job could never run) | feat/ci-env-file | **MERGED** | 4 · CR → PASS → CR → PASS (CI-ENV-4) | 02f8d45e |
| ACC-4 (frontend 24–25) | feat/acc-frontend | AWAITING RV (`f4d9743a`) | in flight | — |
| `_Runtime` patch double | feat/runtime-patch-double | AWAITING RV (`54b269fa`) | — | — |
| live-harness registration | feat/live-harness-registration | CHANGES_REQUIRED, in progress (`d1313348`) | 1 · CR (HARNESS-1) | — |
| suite-size guard | feat/suite-size-guard | IN_PROGRESS | — | — |
| RV calibration | rv-calibration/seeded-hardcoding | bait CAUGHT as blocking | 1 (d59e017) | never merges |

**All nine backend slices merged:** S1, S1b, ACC-1, V1 phase 1, S2, V2 phase 1, V2 phase 1b, V3, V1 phase 2.

~~Trunk suite: **5121 passed, 1 failed** — the single known pre-existing `test_a_rejected_return_still_opens_no_work_item`.~~ **Superseded, and dated snapshots are the reason to write the date rather than the word "current".** Measured on trunk `b7f07838`: **5,239 passed / 1 failed / 11 skipped / 514 deselected**, plus `ruff check` clean and `ruff format --check` clean on 1,159 files. The one failure is still the allowlisted one — **but it is fixed on an unmerged branch**, `feat/runtime-patch-double`, which takes trunk to **5,245 passed / 0 failed** and empties the allowlist. Frontend: 62 files / 867 tests / 865 passed, the two allowlisted `registry.test.ts` failures remaining.

*Why that allowlisted failure survived so long is itself the finding:* it was **two** defects, and the first hid the second. The `_Runtime` double lacked `patched`, so the test raised before reaching `_open_support`. Give it `patched` and the test runs *into* the place it must never reach — the shipped release disables policy evaluation, `SKIPPED_BY_CONFIGURATION` clears the gate, and the "rejected return" was never rejected. **The test's name had been false for as long as it had been red.**

Remaining: the acceptance gate, blocked on the live suite (see the ruling below), and the frontend items under review.

**V1 phase 2 review notes.** Round 3 closed AMENDMENT-5's implementation. Two things worth keeping: the shared execution-liveness classifier is asserted **by identity** across both surfaces (`case_panel.classify_execution_failure is case_reviews.classify_execution_failure`) rather than by comparing outcomes, which makes divergence *unrepresentable* rather than merely detected on whichever statuses someone enumerated — RV noted the boundary is that identity catches a second *copy*, not a *wrapper*, so a future third surface must be held to "finer, never different". And the `signal_id` asymmetry the slice defended — deterministic where a repeat means *again*, random where it means *somebody else*, since a deterministic approval id would collide with the frozen CAS and hand a second actor a receipt saying they succeeded when someone else's approval went out — was judged better than RV's own advisory, which it withdrew.
*One item carried to the acceptance gate, blocking nothing:* permissive `**fact` doubles still exist in other slices' suites; worth one sweep onto `scoped_fact_double.py` now that it exists.

**V2 phase 1b review notes (PASS, zero findings).** RV verified the three historically fragile items by injection rather than reading: the route walker catches a real collision with a *different* parameter name (3 failed) and passes with the normalisation reduced to identity, so the normalisation is genuinely load-bearing and errs safe (it over-normalises the `:path` converter — false positive possible, missed collision not); removing the idempotency key's length prefixes fails 4, and **the separator test fails on its own merit** rather than via the pinned literal, varying two *adjacent* parts where one carries `|` — both conditions the three earlier attempts kept missing. RV recomputed the pinned uuid5 by hand so the test cannot pass by comparing the function with itself, and checked **AMENDMENT-4's four clauses one at a time against source** rather than trusting my wording: no such transaction exists, the order is as claimed, all three steps are genuine no-ops on repeat, and `ConcurrencyConflictError` is a `RuntimeError` so the outbox retries rather than dead-letters. It also verified the wider property I had not asked for: **170 endpoints across all mounted routers, zero collisions.**
*Four observations, none findings:* the walker covers only `return_platform.api` (widening costs ~4 lines); `relay` still defaults to `None` — the same optional-port shape the omc gap was raised to close, live risk closed by the factory and its test; a pre-existing S1 stale-value re-merge on out-of-order redelivery, not amplified here; and an uncaught `DuplicateKeyError` on a concurrent upsert race that converges correctly but reports an error where a no-op would be truthful.

**ACC-2 review notes (PASS on round 3, five findings across three rounds).** The substance was never in doubt; what took three rounds was the record catching up to the work. Two things from the final round are worth keeping.

RV settled the scoping question — was `ruff check tests/acceptance tests/harness` a scope drawn honestly, or drawn to pass? — with a **decisive test rather than a judgement**: the file that carried the F401 was *inside* the claimed scope. A scope drawn to pass would have dropped `tests/harness` first; this one kept the path and fixed the error. The 14 remaining backend ruff errors live in six files, none of them the slice's, and the ledger states the whole-backend figure out loud rather than eliding it. That shape generalises: **when a scope boundary is contested, look for whether the boundary costs its author anything.**

And it excluded the "dead import that isn't" empirically rather than by reading. `subprocess` appeared otherwise only inside a generated `parent.py` string whose own first line imports it — so the removal was safe only if the child scripts never relied on the parent's import. Re-running the signal proof under `python:3.13-slim`: exit 0, and critically **check 2 passes**, the one that spawns the generated parent and needs a grandchild heartbeat. That is exactly the check that would have gone red.

*One observation recorded as not-a-finding, and it is the honest kind:* the remedy for the three unfalsifiable sentences — a table whose left column is a pasteable command — outruns the change at one point, since nothing structurally stops the next Commands block being written from memory. ACC was right not to build that gate: the only one available is a test that parses planning documents, which item 26 already ruled out and which would be circular. **The residue is inherent to a written record, not a shortfall in the fix.**

## ACC phase 3 — the category-B audit, and a failure shape worse than a blind test

Phase 2 left a category B: *"tests exist, found by name, bodies never read, never injected against."* Phase 3 audited it with fault injection — 20 injections, 2 discarded, 7 tests added. **No blocking defect against a non-negotiable:** DR-11 holds under three injections including one that makes an UNMATCHED artifact genuinely create a record through the outcome signal, and zero-hardcoding holds — resolving facts by `field_id` instead of the configured binding reddens 23 tests.

### The dominant finding is not blind tests. It is mis-pointed rows.

**Six guarantees are pinned by tests in files the category-B rows never name.** Read literally, the status record credits `canonical_edit_version` and autosave-after-`APPROVING` to 93 named review-gate tests that stay **96/96 green** when either check is deleted; and credits *"the transcript entry is appended once"* to a test that stays green when the append-once guard is deleted, because it drives a double with its own dedupe — the double supplies the very guarantee under test.

**Why this is worse than a blind test, in phase 3's own words:** coverage is real; the map is wrong — *"a future auditor deletes the guard, sees the named suite green, and concludes the guard was dead."* A blind test fails to catch a defect. A mis-pointed row actively argues for removing a working guard. **The remedy is not more tests but a corrected map**, and the audit's value was mostly in producing one.

### Two real holes — production correct in both, coverage defective in both

1. **Sent ≠ frozen payload.** Every delivery test approves a review that has **no canonical edit**, so the frozen payload and the raw draft are byte-identical and the choice between them is unobservable. An injection sending the raw draft left **5,235 tests green**. *Business consequence:* an associate's edit is hash-verified at approval and **the original draft is sent to Support behind a valid receipt** — the precise failure F2's frozen-payload rule exists to prevent, invisible to the entire suite.
2. **A refused request could write a durable command.** The four 404 tests assert **status only**. Deferring the 404 until after `record_command` left **5,237 green**. *Business consequence:* a principal who cannot see a case receives a correct 404 **while their answer sits on file, queued for delivery to Support.**

The seven added tests are each injected against, and the sent-payload one **asserts its own premise** — that the canonical edit and the draft actually differ — so it cannot decay back into vacuity. That is the right shape for a test closing a hole of this kind: it fails if the *conditions that make it meaningful* stop holding, not only if the guarantee breaks.

### The remedy: a falsifiable map, and the two disciplines that keep it honest

The mis-pointed-row finding was turned on the record that produced it. STATUS's category tables **are** a map of exactly the kind found to be wrong, so phase 3 replaced trust with a check: ~18 rows, one per guarantee actually injected against, each naming **the mechanism to delete and the test that reddens** — e.g. *delete `review_aggregate.py:751` → `test_approval_refuses_a_stale_canonical_edit_version` reddens, that one test only, while the 93 in the named review-gate files stay 96/96 green.* A row naming a file where tests were found asks for trust; a row naming a deletion and its consequence is checkable in one command. Cost was near zero — every entry had already been measured.

**Two disciplines were applied deliberately to stop the map becoming the next over-trusted artifact:**

1. Every test name was re-verified with `grep -rn "def <name>"` against `tests/` rather than transcribed from run output — the same rule that caught interrupted work four times on this run.
2. **Guarantees not injected against are absent, and the table says absence means *unverified*, never *fine*.** A completeness-implying map would recreate precisely the defect it exists to fix.

That second point is the general lesson and it outlives this audit: **a map's honesty lives in what it refuses to imply about its own gaps.**

### Handed to the gate's owner

`test_a_rejected_return_still_opens_no_work_item` is red on the merge tip: its `_Runtime` double never grew a `patched` method when production grew a `workflow.patched` call. Production correct, harness stale — **ACC-2's handed-off finding recurring in a third file.**

**The consequence, corrected by RV and larger than first reported.** Phase 3 initially wrote *"one branch of item 20's deploy-replay pair is unexercised."* In fact `_Runtime` has no `patched` attribute at all, and `return_case_workflow.py` calls `workflow.patched` at **three** sites guarding **three distinct gates** — `_PATCH_V3_CLARIFICATION_ROUND_TRIP` (1672), `_PATCH_STRUCTURED_SUPPORT_DRAFT` (2247), `_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE` (2294). Since `patched` appears nowhere in that 51-test module and is never monkeypatched in, any test reaching any site raises `AttributeError`; 50 of 51 pass, so **none of the 50 reaches any site.** The correct statement is **three gates, six limbs, none exercised.**

*Why the correction matters more than the arithmetic:* as first written, the record would have sent the harness owner to fix one branch and then believe they were done — **a sixth of the work, followed by false confidence.** An understated finding misdirects its owner exactly as a mis-pointed row does, one level up. Fitting thing to have gotten wrong in this particular audit, and it was caught because RV re-derived the claim instead of accepting it.

*Not reached, and recorded as unexecuted rather than green:* AMENDMENT-5's four retry-409 tests, item 20's replay suite, item 8's prompt-injection fixture (read, never injected against), `graph:`/`literal:` bindings, ~85 other review-gate tests, and 20 further ladder scenarios.

## ACC phase 4 — the frontend items, and a ruling of mine that was never executed

Items 24–25 were recorded by phase 2 as *"outside this dispatch's scope as written — backend tests only"*, i.e. **not reached**, never green. Phase 4 audited them with the same instrument. Suite 61 files / 858 passed → 62 / 865. Seven tests added, each injected against, each asserting its own premise; two test files changed, **no production code**.

**Three holes closed, and the first is the one that matters.** `conflict_present: true` appeared in **no fixture under `src/`** — so removing its effect on the Send control left all 858 tests green, and then removing the conflict banner *itself* also left all 858 green. Production is correct, the backend still refuses at the CAS, so nothing wrong is ever *sent*. What was unprotected is the associate **being warned instead of meeting a bare 409**.

The other two are textbook vacuity. **Hash stability was pinned against the wrong clock** — the existing test reads back-to-back, so a per-second leak on a declared field was invisible to it. **Principal independence had nothing at all**, and the obvious test would have been vacuous, so the new one seeds an `accepted_commands` entry first — *giving the comparison something to be wrong about.* That phrase is the whole technique: two identical fixtures agreeing proves nothing.

### ⚠ AMENDMENT-6 was ruled and never executed — my failure to track

All three retired panel fields (`support_digest`, `clarifications`, `parked_messages`) are **still on the DTO, still in the published OpenAPI, and still in the mock.** And because `contracts:check` regenerates from the live FastAPI app and passes its `git diff --exit-code`, that is **a measured fact about the running backend, not a stale document.**

I authored that amendment — retiring three fields a registered section cannot write — recorded it in §1a, and never tracked it to execution. **A ruling with no follow-through is indistinguishable from a ruling never made**, which is the same shape as rule 13's guard with no gate, one level up in the process rather than in the code.

**RULED: sustained, BLOCKING, rule 2 (contract drift).** RV verified it by reading source rather than the audit's report of it — all three fields on the DTO at `operations/case_panel.py:205-208`, hardcoded empty in the composer at `api/case_panel.py:112-115`, present in `CasePanelView.properties` in the published OpenAPI, and in the mock. **The V1 comment the amendment quotes as describing "a connection that does not exist" is still there word for word.**

*Method note worth keeping:* RV tried `contracts:check` independently, and killed it after it blew a five-minute budget in the Python export. It then observed that **reading the DTO is the stronger evidence anyway** — the published document cannot be stale relative to its own generator input, so checking the source the generator reads beats checking the generated artefact. A blocked verification route replaced by a better one, rather than by a weaker one plus an apology.

**Owned by V1/V3 for the code, and by me for the tracking failure.** Queued as the next dispatch; it went unnoticed until someone tried to use the thing.

**EXECUTED** on branch `feat/amendment-6`, commit subject *"refactor(panel)!: execute AMENDMENT-6 — retire the three unfillable DTO fields"*. All three fields are off the DTO, off the composer, out of all four published OpenAPI copies, out of the generated types and out of the mock; the V1 comment is gone with them.

*Cited by subject and branch rather than by sha, deliberately.* This block first named `dafd8a07` and then `b7e0a529`; both were orphaned by routine rebases onto a moving trunk inside a day, and `b7e0a529` was already unreachable by the time the review asking me to cite it was written. That is exactly the stranded-by-sha failure this file records three instances of today, so the pointer here is the one thing about the work that a rebase cannot move. After merge, `git log --grep` on the subject, or `.plan/tracks/AMEND6.ledger.md`, resolves it to a sha that is stable.

Before deleting anything the branch checked whether the amendment had been overtaken — whether any of the three had since acquired a writer — because an amendment executed past its own justification is a different defect. It had not: every one of the nine `register_panel_section` calls in the repository is in `backend/tests/api/test_case_panel_and_reviews.py`, no production module contributes a section at all, and the single `CasePanelView(...)` construction hardcoded all three. So all three were retired rather than some, and the check was run rather than assumed.

The one production reader was `clarificationModel.ts`'s second vehicle, migrated in the same commit as `.plan/reviews/V3f-1.md:316` asked; `support_digest` and `parked_messages` had no readers anywhere. The test whose subject was the retired vehicle was inverted into a guard rather than removed, so the retirement now has a watcher in `frontend-tests`. The size floor is not restaked, because this branch's collected-count delta is zero on both suites: at the branch tip backend reads **5256** and frontend **867**, and the backend rise from the 5251 measured at the original base is RUNTIME's five tests arriving on trunk mid-flight, not this branch's doing. Ledger: `.plan/tracks/AMEND6.ledger.md`.

### Rule 13 again — the accessibility sweep no workflow runs

Confirmed by RV: no `playwright`, `test:e2e` or `axe` reference anywhere in `.github/workflows/*.yml`, and vitest's `src/**` include cannot reach `tests/*.spec.ts`. **The repository's only accessibility sweep is invoked by nothing.** Not ACC-4's — it authored neither the spec nor the workflows. **It belongs to whoever owns `checks.yml`, which is me.** Queued.

### ⚠ A gate can report green having not run

Under load, `npm test` reported `Test Files 40 passed (40)` while **21 of 61 files never started.** Note the shape: not "21 failed" — vitest *believed there were forty*. The headline is internally consistent and green. On an unloaded machine I verified the same suite correctly reports 62 files / 867 tests, so this is behaviour under resource pressure rather than a permanent miscount.

**Why it is not merely a curiosity:** `assert_known_failures.py` fails on any unnamed failure and on a named failure that started passing — but **neither check can see a test that never ran.** A runner under memory pressure could pass `frontend-tests` having executed two thirds of the suite. The backend job has the same exposure in principle. Dispatched as a ratchet in the established `bundle-budget.json` idiom, with the distinction that decides the design: **a suite that shrank because someone deleted tests is a diff to review; a suite that shrank because a worker died is an infrastructure failure reporting green.**

*Also found, rule 13 again:* the repo's only axe accessibility sweep is Playwright-only and **run by no workflow**.

*Not reached, stated as unexecuted rather than green:* everything above was measured against the **MSW contract surface, not the backend that implements it**; plus parked reprocessing in stream order, the two-viewer edit-store scenario, `conflict_present`'s participation in the hash as opposed to its rendering, a11y beyond the conflict surface, and the panel load test — so `copilot.case_poll_interval_ms = 10_000` remains **ungated by measurement**, exactly as the contract's cost posture warned.

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
5. **I dispatched the harness fix TO THE REVIEWER who had just reviewed it.** The message opened by reporting RV's own verdict back to RV, then asked it to correct the harness track's ledger and write the per-module runner. Two rule-11 ownership breaches and a charter violation in one message.
   **RV refused, and its second reason is the one worth keeping.** Ownership was the lesser objection: *"if I write the per-module runner, no one reviews it. I'd be the reviewer on it next round, auditing my own work."* It declined a task it could have moved fast on — it already had the suite loaded — because taking it would have collapsed the separation the whole run rests on. **A reviewer that accepts author work is not being helpful; it is quietly removing the gate.**
   It also corrected the dispatch's stale sha (I said the tree was "intact at `00471116`" when trunk had moved) and noted that would have been the ninth instance. And it forwarded the live measurements it had already paid ~3 minutes per run for, rather than letting the re-dispatch re-derive them. *Refusing the work and still handing over everything the right owner needs is the correct shape of a refusal.*
6. **I merged a branch on a PASS issued against a superseded head.** RV passed `feat/ci-env-file` at `c6b69992`; I had moved it twice since, and my head-correction message arrived after it concluded. Caught before merging. **A verdict names a sha for a reason: a PASS on a superseded head is not a PASS on the branch**, and the fix is a short round on the delta, not a judgement that the delta looked harmless.

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
- **~~Skipped on the platform that runs it~~ — RETIRED, and the correction is the lesson.** Recorded when ACC-1's behavioural stop/kill pin was Windows-skipped and a structural pin was added beside it. **Accurate then — no CI existed. Obsolete now:** CI runs every job on `ubuntu-latest`, so that pin executes on every push. ACC drew the distinction I had missed: *a `skipif(os.name == "nt")` is not this shape when the platform that runs it is Linux — that criticism belongs to a guard whose **only** runner skips it.* Kept struck-through rather than deleted, because a checklist entry that was once true and quietly stopped being true is itself one of the shapes.
- **A guard that looks one level shallower than the reader descends.** V2's wrong-casing notice inspected the payload root and one level; the readers go two deep for `records[].artifacts[]`. A depth-2 snake payload is correctly *dropped* by the strict reader while the notice stays silent — so the card renders zero artifacts and reads as "Support has attached nothing", which is the invisibility the notice exists to prevent, one level below where it looks. **A detector must reach as far as the thing it protects.**
- **A record asserting a green it never ran.** ACC's ledger stated three results — a test count its own addition had moved, a `see below` pointing at nothing, and `ruff check clean` when ruff reported an unused import in the very file under discussion. RV: *"written from memory in the entry describing the fixes for writing things from memory."* The slice's own generalisation is the sharpest statement of this run's theme: **a record asserting a green it never ran is the same defect as a guard nothing invokes** — and *"`see below` with nothing below is the prose equivalent of a `not in` assertion."* Figures in a ledger belong to the same discipline as assertions in a test: produced by execution, pasted, and **scoped to what was actually run** — an unscoped "clean" would have been the same finding twice.
- **A guard nothing invokes — including one written by the branch that made this its theme.** ACC's `posix_signal_proof.py` is deliberately uncollectable and was run once by hand; the branch whose subject is rule 13 shipped a guard with no gate. Its write-up also failed in the opposite direction, never noting that **CI runs on `ubuntu-latest`**, so the Windows-skipped pin it substitutes for *is* gated on every push. **Understating your coverage and overstating your risk are the same error**, and a report can do both at once.
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

**The bundle-budget decision is SETTLED and the section above is superseded.** It read, until now, that `check:bundle` failed at 278.5 kB against a 260.0 kB budget, that picking a new number was a product decision the workflow had no business making, and that `vite build` and `check:bundle` were therefore gated by nothing. All three clauses are stale. `frontend/scripts/check-bundle.js` is a **ratchet** now — it compares each build against measured values in `frontend/bundle-budget.json` and fails on growth — so there is no number left to pick and no reason left not to gate it. Both steps run in `frontend-static`, decomposed rather than collapsed into one `npm run check`, because `&&` short-circuits (a lint error would mean the bundle is never measured) and because a failing step names itself on the summary line.

**Also added since:** a `backend-static` job gating `ruff check` and `ruff format --check` over the whole surface `scripts/linux/03_run_backend_quality.sh` defines — not just `backend/`.

## ⚠ RULE 13, TURNED ON THE GATE I WROTE — the backend job has never run in CI

RV found it while reviewing something else, and it is the sharpest instance of the pattern yet, because the guard is mine.

`backend/tests/conftest.py:29` **hard-raises** when the repository-root `.env` is missing:

```python
if not ROOT_ENV_FILE.is_file():
    raise RuntimeError(f"Required repository environment file was not found: {ROOT_ENV_FILE}")
```

`.env` is gitignored (`.gitignore:25`) and untracked — verified: `git ls-files --error-unmatch .env` says no such path. So on a fresh runner, after `actions/checkout`, `pytest_configure` raises **before collection**, pytest exits **3**, and my own exit-code discriminator reports *"pytest exited 3 — the run failed, not the tests"* and fails the job.

**Consequence, stated plainly:** the `backend` job has almost certainly never completed a run since it was wired. Every *"green on this commit"* claim in `checks.yml`'s comments rests on **local replication on a Windows workstation that happens to have a `.env`**, not on the pipeline. The allowlist self-test, the frontend jobs and `contracts` are unaffected — none of them import that conftest.

This is precisely rule 13's shape and I wrote it: **a gate whose green nobody had watched arrive.** The gate exists, it names itself correctly, its comments are careful — and it cannot execute.

**The fix is settled, and it was measured rather than argued.** `cp .env.example .env` in the job:

| `.env` source | outcome |
|---|---|
| absent | INTERNALERROR, exit **3** |
| the real `.env` | 1 failed, **5197 passed**, 10 skipped, 512 deselected |
| **`.env.example`** | 1 failed, **5197 passed**, 10 skipped, 512 deselected |

*Those two rows were measured on an earlier trunk and are kept as the original evidence. Re-measured on the merged trunk, and independently reproduced by RV: absent `.env` → INTERNALERROR, exit **3**; `.env.example` → **`1 failed, 5232 passed, 11 skipped, 514 deselected`**, exit 1.*

**One precision the comparison does not support, and the record should not imply it:** the two files are **not** interchangeable in content — a developer `.env` carries ~11 extra live-infra and host-port keys. What is established is that the **normal suite's outcome** is the same either way, which is all CI needs, since CI only ever sees `.env.example`.

**The same outcome** — down to the single allowlisted failure and the exit code the allowlist step then passes. (This said "Byte-identical", sitting immediately after the paragraph that disclaims interchangeability; the two sentences contradicted each other on a careless read, and the weaker one is the true one.) `Settings` accepts the placeholders, and the normal suite has live-infra deselected so nothing dials a real service.

**Measured precisely, since "~11 extra keys" was itself approximate:** `.env.example` carries 124 keys, a working `.env` 135; **exactly 11** are env-only and **zero** are example-only. The example is a strict subset — which is the actual reason copying it suffices, and a stronger statement than the outcome comparison alone supports.

**Both alternatives I proposed were rejected on evidence, and the first rejection is the one worth keeping.** A committed `.env.ci` is ignored by `.gitignore:31`'s `.env.*` rule — and the comment above that rule records *why it exists*: `backend/.env.vault-backup` once carried a live provider key into git history and was caught by push protection. Adding `!.env.ci` would **punch a hole in a guard installed after a real credential incident, to solve a problem a tracked file already solves.** A degrading conftest was rejected too: the raise is a deliberate guard making a missing `.env` loud rather than letting tests run against silent defaults, so copying satisfies it honestly while degrading weakens it — rule 13 in spirit.

And it is not an invented command line, which is `checks.yml`'s own stated principle: `scripts/bootstrap_host.sh:17` and `reset_docker_environment.sh:85` already run exactly that copy, and two further scripts instruct developers to.

~~`ensure_runtime_env_keys.py` maintains `.env.example` as the authoritative key set so it cannot silently rot.~~ **Struck: it does not.** Its `update(path, example_path)` reads the example and writes into `.env` — one way — and never inspects the example. I wrote this into the fix's own rule-13 answer, which is the worst place for it: *a claim about what stops a thing rotting, that names a mechanism which is not doing that job.* RV caught it; I confirmed the direction in the source rather than accepting the correction.

**The real protections, all four stronger than the one claimed:** deleting `.env.example` fails the `cp` in the job; dropping a key fails `Settings` (`frontend_cors_origin`, `mongo_dsn`) or `conftest`'s `_required_environment_variable` **by name**; drifting its content fails `tests/test_ai_gateway_routing.py:194`, which reads the tracked example and asserts over it; and `ensure_runtime_env_keys.py` is itself gated, by `tests/test_runtime_env_key_sync.py`. **Every one is a backend-suite test — so every one was equally unreachable in CI until this step existed.**

*And the thing worth keeping:* `test_ai_gateway_routing.py`'s own comment says it *"used to pass or fail on a file nobody reviews and CI never sees."* Somebody hit this exact defect class before, wrote it down, and fixed it in one test — while the job that would have caught it everywhere sat unable to start.

**Two conditions on whoever implements it:** `contracts` is likely exposed the same way since it imports the app — verify it too. And **prove the fix by watching the job fail first**; a fix for a gate that has never run is exactly the claim that needs its red observed before its green.

**Dispatched separately and reviewed on its own** — not folded into the lint branch that found it. Ordering matters: both touch `checks.yml`, so the lint branch merges first.

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

**Nine independent agents have now read the branch ref instead of the sha they were given, and all nine were right to.** Instances include a snapshot naming a commit **reachable from nothing in the repo**, and worktrees arriving checked out **837 and 847 commits behind** on ancestors of trunk — where branching as instructed would have silently omitted every slice merged this run.

RV's judgement: *"seven independent agents reading the ref instead of the number isn't seven lucky catches. Worth fixing at provisioning before one of them doesn't notice."*

**Why it is dangerous rather than annoying:** an ancestor base **fails silently**. The work compiles, the suites pass, and the slice is simply missing everything merged since — there is no red to notice. Contracts §3 now makes ref-verification mandatory, which is a mitigation, not a fix. **The fix belongs where worktrees are provisioned.**

**The mechanism is now identified, and it is not random.** Instances 8 and 9 were both **agent worktrees under `.claude/worktrees/`**, arriving at 837 and 847 commits behind respectively — the two largest gaps recorded. That is not drift; it is a worktree provisioned from a stale point and never advanced. The number keeps growing because trunk keeps moving while the provisioning point does not, which means **the next instance will be worse than this one, and the failure will stay silent as it grows.** Every agent dispatched into a worktree must verify against the ref; the dispatch template now says so in its first paragraph, and both agents that hit it caught it there.

*Related, and found by causing it:* those same worktrees are real repositories inside the working tree, so `git add -A` stages them as gitlinks — a submodule pointer to a clone nobody else has. Now ignored (`63744f2a`).

## ⚠ Bears on the acceptance run — live-suite flakiness under load

The harness repair recorded a residual it explicitly did **not** claim to have fixed: running just **two** real-infra files **in one process** is flaky — 1–2 failures per run, a **different test each time**, all passing in isolation. It attributes this to the load signature that file's own docstring diagnoses, and states it is pre-existing rather than caused by the repair.

**Why it matters beyond that branch:** the sanctioned entry point (`run_real_infra_suite.sh`) runs **all 512 live tests in one process**, so the acceptance gate will meet this at far larger scale than two files reveal. A gate that fails a different test every run is indistinguishable from a gate that found something — which is precisely the ambiguity this run has spent itself eliminating. **RV is asked to rule whether "pre-existing" is established or assumed, and whether this blocks the acceptance run.**

### ⚖ RULED (HARNESS-1) — it blocks. The acceptance run cannot be trusted as it stands.

RV ran live tests throughout — eleven runs against a healthy stack — and the ruling rests on measurement, not inference.

**The residual is materially worse than the branch recorded.** The ledger said "each passes in full alone." Three consecutive runs of the offending file **alone** gave **5, 4 and 1** spurious failures (487s / 409s / 303s, against 13-passed-in-73s on a fresh server). Spurious failures appeared at **every** load tested — 1 file: 5/4/1; 2 files: 0/0/1; 5 files: 3/1.

**And they were all in one module.** Every single spurious failure across every run was in `test_return_case_workflow_real_infra.py`. A 512-test single-process run creates strictly more of the accumulated server state that drives this, so it will return a **non-empty, non-repeating failure set essentially every time** — each entry indistinguishable from a real regression.

**"Pre-existing" is asserted and cannot be established:** at base, 16 of those 21 tests never finish, so the control run does not exist. **The repair is nonetheless exonerated, on other evidence** — the diff adds no infrastructure load, and the four untouched live modules (32 tests) were green in all eleven runs. *Exonerated by evidence, not by an unavailable control* — the distinction matters, because "we could not test it and nothing broke" is not the same claim.

*Related correction:* the branch's "17 failing tests" is **16**. RV measured the policy-gate file three times at base: 4 failed / 4 passed every run, not 5/3. The likeliest explanation is that **a flake was counted into a defect total** — the same confusion this residual causes, at small scale.

**Remedy chosen: run the live suite per module, in a fresh process per file.** RV named two sufficient fixes; the other was quarantining the module. Quarantine removes coverage to make a number look clean, while per-module execution keeps every test and changes only the execution model — nothing is hidden. Dispatched into `scripts/dev/run_real_infra_suite.sh` with one non-negotiable: **the aggregate must not be able to lie.** Many exit codes now exist where there was one, and a runner that exits 0 because the last module passed would be a fresh instance of this run's oldest defect.

*Unverified and not claimed:* the remaining ~490 live tests. RV scoped its verdict to what it observed.

### ✗ THAT REMEDY FAILED, and the evidence was in the brief I wrote

**Per-module execution does not fix it.** Three isolated runs of the module gave `1 failed / 2 failed / 0 failed`, a different set each time, two of the names appearing in none of RV's eleven prior runs — the failure set is not converging on weak tests, it is drawn fresh.

**The error was mine and it was avoidable.** Per-module execution *is* "one file, one fresh process" — precisely the condition RV had already measured six times at head and found failing in three (1f/3f/1f). I had that data in hand, wrote it into the dispatch, and chose a remedy it had already falsified. The agent's own verdict on the same miss: *"this was visible in the brief's own numbers before I wrote a line of script, and I didn't see it until the runs came back."*

**The mechanism, now stated properly:** a fresh process resets *in-process* state — client, worker, event loop. The accumulation is on the **shared Temporal server every process talks to.** Isolating a client from itself does nothing about what it talks to. Any remedy must act on the shared server, not on process boundaries.

**The per-module runner is kept anyway**, on what it did earn rather than on what it was built for: bounded per-module timeouts (one hanging module no longer hangs the gate forever), and an aggregate that cannot lie. That second property found a real defect on its first proof run — the count regexes required a non-digit before the number, and pytest's summary opens with one, so every all-green module parsed as unreadable and every leading `N failed` was dropped. It printed `tests failed: 0` beside a failing module. **Shipped on the theory, it would have reported no failures next to a red exit** — the guard that refuses to call unreadable counts a pass is what blocked the false green.

*Also repaired in passing:* the collected-total report was already broken — `tail -1` was picking pytest's capture-warnings docs URL, so the whole-suite run printed a URL where its total belonged.

### ⚑ THE MECHANISM, FOUND — and it was written down in this repository in August

**It is not accumulated server state. It is a fixed wall-clock budget meeting a loaded machine.**

`test_return_case_workflow_real_infra.py:320` defines `reached(name, *, within_seconds=30.0)`, whose timeout path raises at **line 333**: `f"{name} did not run within {within_seconds}s"`. Twelve call sites in the module, several tightened to 20s. **Every test named as spuriously failing, across all three independent investigation rounds, routes through that helper.**

`docs/execution-context/remediation/LEDGER.md:445` — dated August, **eight days before this run began** — records the same tests failing the same way and concludes: *"a fixed wall-clock budget is the mechanism; contention is the cause."* It also records this exact trap being fallen into once before, when a slowdown blamed on leaked containers turned out to be the suite starving its own I/O.

**Confirmed from the investigation's own captured output, unread at the time.** Step:09's failure record for `test_a_rejected_return_needs_no_graph_sync` reads `tests\test_return_case_workflow_real_infra.py:333: AssertionError`. **Line 333 is that raise.** The agent's verdict on itself: *"I had the message and did not read it."*

So "a different set each time" was never evidence of arbitrariness — it is **the population that waits on a wall clock**, failing in a shuffled order under load. Two facts that never fitted accumulation now fit easily: 53+ modules with zero spurious failures and no upward drift in per-module time, and the failures appearing only in the rounds where the machine was busy.

**And the contention was mine.** Three and four concurrent agents, several running full backend suites, throughout every round in which these failures were measured — including RV's eleven runs and step:09's three. Nobody recorded it because ambient load is invisible. **RV's fresh-versus-loaded table measured a real effect in which "loaded" meant *the machine*, not the namespace** — the same numbers read as evidence for server-state accumulation are equally readable as contention, and the two were never separated. My server-state hypothesis was a worse guess than the one already in this repository, and I dispatched an agent to chase it.

### ⚑ THE RULE THAT WOULD HAVE PREVENTED ALL OF IT

**A flake investigation that records names and not messages is not an investigation.**

Across fourteen measured runs — RV's eleven and step:09's three — **no failure message was ever captured. Only test names.** `did not run within 30.0s`, an assertion failure, and a connection error are three unrelated defects; every round discarded the one field distinguishing them and then reasoned about the residue. Three remedies were proposed and one implemented before anyone read a message. The mechanism was recoverable at all only because an August ledger happened to record one.

### ⚑ RESOLVED — and the August entry was itself half wrong

**The mechanism is a fixed test budget smaller than one term of the schedule it waits on.**

`start_to_close_timeout` is **per attempt**, not per activity. `_PERSIST_TIMEOUT` is 30s, `_BEST_EFFORT_RETRY` allows 2 attempts, backoff ~1s — so the bay step alone is bounded at **61s** by construction, and the path the test waits on runs through `_PERSIST_RETRY` at 5 attempts: **165s**. The test budgeted **30.0s** for the whole workflow. *The test's entire budget equalled the ceiling of a single attempt of one step it had to wait through.* It has been asserting on the coincidence that a failing attempt fails fast since the day it was written.

**Pinned by evidence, not argument.** Twelve instrumented runs on an idle machine came back **bimodal — ~3s or ~33–46s, with nothing between.** A continuum would be contention; two clusters separated by almost exactly 30s is a timeout firing, and 30s is `_PERSIST_TIMEOUT` exactly. Then the count that settles it: **every slow run recorded 8 activities, every fast run 9, twelve of twelve.** The missing call is the second bay attempt, which burns its full per-attempt ceiling.

**The August ledger was right about the mechanism and wrong about the cause.** It said *"a fixed wall-clock budget is the mechanism; contention is the cause."* Five failures in twelve runs, and six in eleven, **on a quiet machine** — contention is a modifier, not the cause. *A prior finding is evidence, not an oracle.*

**Two hypotheses killed by control, one of them mine.** Reusing a single task queue failed **worse** than fresh queues (3/6 against 2/6), which exonerates queue churn — and disposes of "a unique namespace or task queue per module", a remedy shape **my own brief named as likely**. A dispatch that lists candidate remedies can send an agent down a path the evidence never supported.

**The production question, escalated and then answered by mechanism.** An activity was accepted and never completed. The discriminating event: `ACTIVITY_TASK_TIMED_OUT / TIMEOUT_TYPE_START_TO_CLOSE`, with **`s2s=0s`** — `schedule_to_start` unlimited. Had the *server* failed to deliver, the task would have queued **forever** rather than timing out at 30s. **The alarming reading is excluded by the structure of the observation, not by absence of evidence.** Downgraded to a worker-side stall — which is what `start_to_close` exists to bound and what production retries, leaving a case *delayed and visible in history* rather than silently stuck. Why a worker inside a live `async with Worker(...)` accepts a task and does not run it is **left unexplained rather than fitted to a story**, since the poller-churn account does not cover the fresh-queue failures.

### The remedy, and the three sites it deliberately does not touch

`LIVENESS_CEILING_SECONDS = 180.0` — 165s construction bound plus a 15s margin derived as ~3× the worst observed fast path (2.86–5.29s), deliberately small beside the figure it modifies. Every term is read off the imported `RetryPolicy` objects, so **the derivation lives in code and moves when production moves.** Uniform across the 14 sites by design, with the docstring saying why, so nobody refines it into fourteen fragile per-site derivations — the exact error caught mid-investigation, when the first bound was derived from *the activity that happened to fail in front of the investigator.*

**Why the cost objection dissolves:** a `reached()` wait returns as soon as its condition is met. The budget is a **ceiling, not a duration** — so a larger bound slows failing tests only and never passing ones.

**Three sites keep their 20s budget, because they are asserting promptness, and the file says so** at line 574: *"`bay_wait_seconds` is 30 here and the test does not take 30 seconds: that is the assertion."* Raising those deletes the regression check the bay activity was written to provide — invisibly, since the tests would still pass. **This is why the per-site read was mandatory rather than diligent.** A fourth site pattern-matched to that group and was different on reading: *classifying by timings alone would have been wrong safely; by budget alone, wrong dangerously.*

*Scope corrected twice:* 12 sites in the module, not 13 — a grep that counted its own `async def reached` definition, then repeated three times because it was already written down. And **17 across two modules**, because the sibling carries its own copy of the helper. **That duplication is now 2-for-2 on causing defects** — once when the probe list rotted, once here. Collapsing the two implementations is registered, not done.

### ✗ THE REMEDY FAILED, AND IT WAS NEVER ONE DEFECT

**One clean run in five.** Run 5 failed with `did not run within **180.0s**` — the ceiling derived from the retry schedule, exceeded at one of the fourteen sites it was applied to. **Two successive derivations, 61s then 180s, each falsified by the next measurement, is evidence the quantity is not bounded by the retry schedule at all.**

**With messages finally captured, there are at least four distinct signatures:**

| | signature | note |
|---|---|---|
| (a) | the derived ceiling, exceeded | the only one the wall-clock diagnosis addressed |
| (b) | a workflow-history assertion — `no failed workflow task; last event types: 7, 10, 11, 12, 5, 6, 7, 10` | **no budget involved at all**; may be a product defect |
| (c) | `RPCError: h2 protocol error: http2 error` | a **transport** fault on the gRPC connection, never seen in this track before, plausibly bearing on **every** live module |
| (d) | the graph-sync test, twice | the only repeat offender — and the one whose message was lost |

**So the convergence that drove three rounds was true and insufficient.** Every failing test did route through `reached()`. That fact cannot distinguish (a) from (b), (c) or (d) — **names never could**, which is the whole point, arriving one level deeper than when it was first stated.

**The ceiling is reverted, all fourteen sites**, including the policy-gate module that went green twice under it: *that module was green before the raise as well, and nothing ever recorded it as flaky, so two greens without a control is a correlation.* Keeping fourteen because two look fine is the error the reversion undoes. **Reverting is not a retreat** — the per-attempt fact remains true, but it is one of four signatures, and re-raising requires a derivation that *survives* measurement rather than one that predicts it. **A third derivation would be fitting a number to data.**

*Kept from the attempt:* twelve lines of comment marking the three promptness budgets as **assertions, not liveness nets** — placed at the call sites, because *a ledger entry is not what a future reader has open when they decide to finish the job on three budgets that look unfixed beside eleven others.*

### ⚑ ROOT CAUSE — the storage layer, underneath everything this track tried

**Temporal's Postgres pays ~126 ms per WAL flush on this hardware**, against a sub-millisecond expectation. With `synchronous_commit=on`, **every Temporal transaction pays it.** The unsync'd path runs at **1,087,454 ops/sec** — the data path is fine; it is *durability* that costs.

The chain, server-side throughout: `shard status unknown` arrived from `get_workflow_execution_history`, not from a test; and Temporal's own logs over three hours carried **145** `Failed to start transaction`, **266** `context deadline exceeded`, **44** `shard status unknown` and **19** `Acquired shard`.

> **⚠ TWO CORRECTIONS TO THIS SECTION'S ORIGINAL EVIDENCE — both were mine to propagate, and the first was the headline.**
>
> **The 175-second checkpoint was not pathology.** This section led with `checkpoint complete: wrote 1830 buffers; write=174.354 s` and read it as disk starvation. **`checkpoint_completion_target = 0.9` means Postgres deliberately spreads a timed checkpoint's write phase over 0.9 × 300 s = 270 s, precisely to avoid an I/O spike.** A 175 s write inside a 270 s budget is Postgres working as designed. The phase that synchronously touches disk is `sync=` — **3.0 seconds.** Proved independently: unspread, the same database wrote 820 buffers in **0.076 s**. *Normal behaviour misread as pathology — the largest number in the log was reached for as an illustration and never checked for what it meant.*
>
> **And the wrong row of the right measurement was quoted.** "30–406 ms" spanned the whole `pg_test_fsync` table. The server uses `wal_sync_method = fdatasync`, whose measured rate is **7.949 ops/sec — 126 ms per flush**, four times worse than the `open_datasync` row originally cited as relevant.
>
> **The mechanism survives, correctly located: WAL commit latency, not checkpoints.** The relocation makes it *more* explicable, not less — a per-transaction cost fits Temporal's error profile better than a periodic one ever did.

**Ruled out by measurement, not argument:** memory (166 MB of 1 GB), bind-mount overhead (it is a named volume), sequential throughput (53 MB/s) — and **accumulated state: the database is 38 MB with 916 executions.** *The hypothesis that opened this investigation, survived three rounds and drove two remedies, died to one query.*

**It explains why every remedy here was doomed.** Per-module execution, namespace and task-queue isolation, and the derived ceiling all acted on the test process or the Temporal namespace. **The constraint is underneath both, and nothing in `backend/tests` can reach it.** It also retires the production escalation for a second independent reason: an activity accepted and never completed is what a worker looks like **when its completion cannot commit.**

### The A/B — approved by the user, and decisive

`fsync=off` + `synchronous_commit=off` on the test Postgres only (a shared `compose.yaml` outside this repo; before-state committed first, and the file is itself under version control so the original is recoverable independently of our ledger).

| | baseline (`fsync=on`) | `fsync=off` |
|---|---|---|
| workflow runs clean | **3 of 5** | **5 of 5** |
| run-time spread | 79–275 s | **39.5–42.0 s** |
| persistence errors in window | 145 / 266 / 44 | **0 / 0 / 0** |

**At the original, un-raised budgets** — so whatever the setting does, it does not do it by relaxing what the tests check. The reverted ceiling was the right call.

> **⚠ THE A/B IS UNINFORMATIVE ON FLAKINESS, AND THE B ARM SAID SO.** Reverting the setting produced **5 of 5 clean as well.** At a ~40% baseline failure rate, P(0 failures in 5) ≈ **7.8%** — unlikely, possible, and it happened in *both* arms. **Neither arm's clean sweep discriminates.** The success criterion had been set without checking whether five runs could clear it, which is a criterion that could only ever confirm.
>
> **A forced-checkpoint experiment was then run instead of buying thirty passive runs — and it failed to build its own condition.** Of 49 forced checkpoints, the first cleared a restart backlog and **all 48 others were trivial** (37–853 buffers, 0.03–0.55 s), because a manual `CHECKPOINT` is immediate rather than spread and firing one every 5 s never lets the dirty set grow. One failure in three runs is indistinguishable from background rate. **Recorded inconclusive, and stopped there** rather than upgraded to a refutation — "the forced checkpoints were too light" and "the hypothesis is wrong" cannot be separated from three runs.
>
> **So: established** — ~126 ms per WAL flush; the suite runs **~2× faster** on every Temporal-dependent module; Temporal persistence errors **zero** across a full run window; and the Mongo-only outbox control **unmoved**, disposing of "the machine got faster". **Not established** — that any of it removes the flakiness. *Nothing in this record should be read as proving that link.*

**The variance collapse is the sharper signal than the pass count** — a 2.5-second band where there was a 200-second spread. The bimodality that first identified a timeout is simply gone, and five runs of pass/fail could never have shown that.

**An unplanned internal control disposes of "the machine just got faster":** the outbox module is Mongo-only and never touches Temporal's Postgres. It did **not** speed up (20.1 s → 25.9 s) while every Temporal-dependent module ran 3–7× faster. The effect is specific to the storage path that changed. The named volume was re-attached rather than recreated, so **accumulated state was held constant across both arms** — the experiment could not confirm the storage fix by also wiping the data.

### The instrument was the defect, again

`repeat.ps1` captured each run with `Select-Object -Last 12`, so three failures in one run compressed to a single partial traceback — **and signature (d), the only repeat offender, has a name and no message.** In the author's own words: *"I wrote the rule that a flake investigation recording names and not messages is not an investigation, and then built a harness that truncates messages. A rule stated in a ledger does not enforce itself; the harness has to."*

**That is the run's most durable lesson about its own method.** Every rule recorded here — messages not names, verify the ref, name the gate — is a rule about what a *tool* must do. Writing it down changes nothing until something enforces it.

*Two orchestrator errors in this stretch, both mine:* I told the agent its repetition had been **killed**, inferring death from an empty process table when the run had **completed** — a process table cannot distinguish *finished* from *killed*, and I checked neither the log's mtime nor the exit status. Had it accepted that, seven of eight runs would have been discarded and `13 passed` reported as the only datum. And **I put two authors on one append-only ledger**, producing duplicate `step:11`/`12`/`13` headings; resolved by appending an index rather than renumbering, with both authors' "intruder" framings withdrawn as symmetrically mistaken about a cause that was mine.

**Standing fact until proven otherwise: the live suite has never once been run to completion. No live-suite result may be quoted.**

### Incidents from that attempt, kept because the rules generalise

- **A running script is a live artifact, and editing it is editing a process.** Step:10's edits landed while bash was still interpreting the same file for a run in flight. Bash parses a compound statement whole — so the loop body kept executing the pre-edit version, which is *why* that run hung with no timeout — but re-reads by byte offset for top-level commands afterward, so that run's final summary block may be read from edited bytes. Per-module lines are pytest's own output and safe; **the aggregate is not, so that run cannot serve as the proof of the aggregate property even if its numbers come out fine.** The proof rests on injections that ran against files nobody was editing.
- **An instrument pointed at the wrong artifact reports confidently, wrongly, and without error.** A run was declared terminated on the strength of a 0-byte launcher stdout — empty *by construction*, since the run redirects to its own log — plus a kill notice for one of the agent's own pollers. Neither instrument pointed at the run or its log. Third instance today, after the wrong-`src` worktree imports and the ruff-outside-the-repo false positives. **The common tell in every case was available and unchecked** (here, the log's mtime was current). What makes this family dangerous is that the reading carries nothing saying which artifact it came from.

## Open items surfaced but not yet dispatched

- **`actorId` optionality — the diagnosis was WRONG, and the agent falsified it before spending on regeneration.** RV's diagnosis (schema non-required → optional TS type), which I passed on unverified, is **not in the causal path**. `frontend/src/api/cases.ts:51` defines `Served<T>` as `{ [K in keyof T]-?: Served<Exclude<T[K], undefined>> }`, which forces required-and-nullable **regardless of the document**. Probe A — hand-patching the `.d.ts` to exactly what a correct regeneration would emit — left **3 errors, same three files**, the `?` intact. Probe B — one line in one fixture — cleared that file: **2 errors**. The real cause: `actorId` postdates the three helpers and is the one field not written longhand, arriving only via a `Partial<>` spread, which TypeScript types optional.
  **This also dissolved my own argument against the obvious fix.** I said patching fixtures "would leave the REST view free to omit the key" — that depended on the fixtures' optionality flowing from the schema, and it does not. `Served<T>` forbids the omission by construction. **Ruled: patch the three fixtures** — MERGED, and **`tsc -b` now exits 0 on trunk**. RV reproduced both probes itself and withdrew its own diagnosis: *"I reasoned from the generated type to the schema without reading the alias sitting between them, and stated it as a finding-grade cause."* It also supplied a cross-check the agent could not claim for itself — the three error coordinates match a baseline **RV recorded in `GUARD-1.md` on a different branch before this one existed**, so the errors fixed are provably the errors originally reported. And it tested the one ordering the agent had not: `tsc` writes `.tsbuildinfo` on success and may not on failure, so remove→red→restore could in principle short-circuit into a stale green — probed, and it genuinely re-checks.
**A2 — this reshapes the deferred work and must be settled before anyone picks it up:** `CaseFactProjection.required` is currently `['factId', 'factName']`. `actorId` is absent — **but so are nine other always-serialised fields.** The dishonesty is not `actorId`-specific; it is *why `Served<T>` exists*. Fixing one field of eleven leaves the document just as misleading at the same six-artifact regeneration cost. **Scope it deliberately before someone reduces it to a one-liner.** Also: the verification instrument must target the **generated** `.d.ts`, not a `Served<>`-derived alias — `Served` strips the `?` regardless, so an assertion against the alias passes vacuously. Same trap, new costume.
**Deferred, recorded under its real justification:** making the Python field required-and-nullable is still defensible — the published document says `actorId` may be absent while the writer guarantees it is always present, so a third-party client would type it optional and write defensive code for an impossible case — but it fixes none of the errors, its `tsc` injection is *unpassable* (any red would be red for an unrelated reason), and it costs five `CaseFactProjection(...)` sites plus six regenerated artifacts. Blast radius that size must be authorised against its actual justification. `CaseFactView` stays alone: `test_case_fact_actor.py:101` pins its required set for a stored-document reason a response DTO does not share.
- **UX-copy inconsistency across panes:** `CasePanel.tsx:198` says "This reply was empty." while `SupportReplyBody.tsx:95` says "This reply is empty. Rebuild it before sending — Support would receive nothing." Same state, two sentences, two panes. Owning slice's to settle.
- **`review.conflict`'s contrast pairing** — the same failing pair at the same tint that V2 found on its own token twin. V1's token in V1's component; registered, untouched by V2, correctly.
- **`animate-bounce` — RESOLVED by restyle** (`39fd7c0`), user-directed. The dots are now a staggered `animate-pulse`: Tailwind's bounce is a squash curve built for scroll-down arrows and disagreed with the `animate-spin` loader on the line above, **and** pulse inherits `index.css`'s `prefers-reduced-motion` rule, which freezes it outright — bounce had no such handling, so the restyle fixes an accessibility gap as well as the visual one. Never suppressed. V2 was right not to invent an ignore format for a plugin that exposes none.

## Rule 13, one layer deeper — the backend quality script nothing invokes

The CI-lint branch was dispatched to gate `ruff` and came back having found the larger version of its own errand. **`scripts/linux/03_run_backend_quality.sh` is the repository's own definition of backend quality** — it runs `ruff check .`, `ruff format --check .`, ruff over three further root paths, `mypy`, and `poetry check` — and **nothing in CI invokes it.** Gating only `backend/` would have reproduced the exact defect one level down; two of those three root paths were unformatted.

**It also corrected the dispatch that sent it.** I had given it "15 errors, 85 unformatted files" in a phrasing that implied one population. Measured: **14 errors across 6 files, 94 unformatted, overlap 2** — and 96 unformatted once the root paths are included. `ruff check` and `ruff format --check` select genuinely different file sets, and treating them as one set is how a fix gets scoped to the wrong thing. It re-measured rather than inheriting either number.

**Option 1 (fix now) was chosen on measurement, not preference:** five unmerged branches touch **zero** of the affected files, so the "large diff landing on top of in-flight reviews" objection was checked and found absent. And the six `B904`s were fixed by **chaining** rather than `from None` — which would equally have satisfied ruff while throwing the traceback away.

**Left explicitly undispatched, and named rather than absorbed:** `mypy` is pinned, configured `strict = true`, run by two developer scripts, and **gated by nothing**. Same for `poetry check` and `pytest scripts/tests`. A strict-mypy debt is a different size of question and was correctly refused as out of track scope.

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
