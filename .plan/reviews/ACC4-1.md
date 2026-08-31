# ACC4-1 — `feat/acc-frontend` @ `f4d9743a`

**Verdict: `CHANGES_REQUIRED`** — one finding against this branch (F1), plus one
blocking contract-drift item **escalated to the orchestrator** (E1) that this
branch found rather than caused and must not be charged with.

Reviewed against trunk `refactor/unified-return-platform` (`b7f07838`).
Scope: frontend only. **No backend test was run by this review**, live-infra or
otherwise, per the dispatch.

---

## Base

`git merge-base --is-ancestor refactor/unified-return-platform f4d9743a` exits
**1** — trunk head is *not* an ancestor of the branch. The merge base is
`2d0e3d65`, which **is** an ancestor of trunk (`--is-ancestor 2d0e3d65 b7f07838`
exits 0). So the base is genuine but **one commit stale**.

The single missing commit `b7f07838` (*"trunk was red on both ruff gates"*)
touches three files, all under `backend/tests/`. For a frontend-only branch the
staleness is behaviourally inert, and FE-DEFECT-2 still holds against trunk
head: `frontend/src/domains/registry.test.ts` is untouched by `b7f07838`.
Recorded, not a finding.

## Diff shape

`git diff --name-status 2d0e3d65..f4d9743a` — 5 files:

| file | |
| --- | --- |
| `.plan/acceptance/STATUS.md` | M |
| `.plan/acceptance/frontend-audit.md` | A |
| `.plan/tracks/ACC4.ledger.md` | A |
| `frontend/src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx` | A |
| `frontend/src/mocks/handlers/casePanelHandlers.contract.test.ts` | M |

**No production source changed.** Claim confirmed.

---

## 1. Suite figures — reproduced exactly

```
$ npx vitest run --maxWorkers=2
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
   Duration  78.87s
```

62 files / 867 tests / 865 passed / 2 failed, and the 2 are FE-DEFECT-2's
pre-existing `registry.test.ts` pair. The branch's headline is accurate.

## 2. The three closed holes — four injections reproduced, not two

### conflict presence — **hole confirmed real, closure confirmed**

The fixture claim holds. At the base commit,
`git grep -n "conflict_present: true" 2d0e3d65 -- frontend/src/` returns exactly
one hit and it is a **comment** in `reviewContrast.test.ts:30`. Every fixture in
`src/` at base sets the marker `false` (`CasePanel.test.tsx:390`,
`ClarificationsSection.test.tsx:632`, `SupportReplyReview.test.tsx:346`,
`casePanelHandlers.ts:148/523`). Nothing anywhere set it true.

**INJ-F2 reproduced.** `TemplateReviewSection.tsx:143`
`gaps.length > 0 || review.conflict_present` → `gaps.length > 0`, full suite:

```
 FAIL  …/TemplateReviewSection.test.tsx > what an unresolved conflict does to Send > blocks it, and names the conflict as the reason rather than a missing field
 FAIL  …/TemplateReviewSection.test.tsx > clearing it > is done by the canonical-edit write, and the panel then unblocks
 Test Files  2 failed | 60 passed (62)
      Tests  4 failed | 863 passed (867)
```

Two new reds, **both in the new file**, and *nothing pre-existing moved*. That
single run establishes both halves of the claim at once: the guarantee had no
watcher before, and it has exactly one now. Matches the record.

### hash stability — **confirmed, including that the old test is blind**

**INJ-F7b reproduced.** `template_review_reminders_sent: 1` →
`1 + (Math.floor(Date.now()/1000) % 2)` in `casePanelHandlers.ts`:

```
 FAIL  …/casePanelHandlers.contract.test.ts > … > holds the ETag across a real wall-clock second, with the deadline ticking
 FAIL  …/CasePanel.test.tsx > what the associate sees > shows the draft, its provenance and the deadline
```

Exactly the shape recorded: the **new** second-boundary test reddens; the
pre-existing `moves the ETag when the panel moves, and holds it when nothing
does` **stays green**, confirming it is blind to a per-second leak; and the only
other red is the incidental "1 of 3 reminders" fixture assertion the branch
already named as a guard that would not exist for a field nobody renders.

### principal independence — **the key one, and the seeding is genuinely load-bearing**

**INJ-F11 reproduced.** `accepted_commands` filtered by `Authorization` in the
panel handler, full suite:

```
 FAIL  …/casePanelHandlers.contract.test.ts > … > serves two principals the same bytes and the same ETag, commands included
 Test Files  2 failed | 60 passed (62)
      Tests  3 failed | 864 passed (867)
```

One test in 62 files catches it, and it is the new one.

**The vacuity question, answered by measurement rather than by reading.** I
removed the seeding `POST …/approve` *and* the premise-2 assertions, left
INJ-F11 injected, and re-ran the file:

```
 Test Files  1 passed (1)
      Tests  20 passed (20)
```

**The test passes while the defect is present.** Without the seed the two
principals are compared over an empty `accepted_commands` and the comparison
proves nothing — precisely the vacuity family ACC3 found twice. The seeding is
therefore not decoration; it is the only thing making the assertion capable of
being wrong.

**And the premise assertion that protects the seed genuinely bites.** Seed
removed, premise-2 restored:

```
AssertionError: expected 0 to be greater than 0
```

It fails loudly rather than passing vacuously. This is the correct construction.

## 3. Premise assertions — spot-checked against their premises failing

Item 2 of the dispatch asks whether the premise assertions actually fail when
the premise stops holding. Two probed directly, both bite:

* **`commands.length > 0`** — above.
* **wall-clock premise 1** — `template_review_deadline_iso: DEADLINE_ISO` →
  `null` in the handler:
  ```
  AssertionError: expected null not to be null
  ```
  A payload with no timer would otherwise satisfy "no wall-clock value"
  trivially and leave the test passing for the wrong reason for ever. It does
  not.

`expectsConflictOnly` asserts on the same `review()` builder `servePanel` feeds
to MSW, so the gaps-empty premise is coupled to the fixture actually served.
Sound.

## 4. F7a — discarding was right

F7a added a computed countdown to the mock body and reddened on
`additionalProperties: false`. Verified the mechanism it actually hit:
`CasePanelView` in the published document carries `additionalProperties: false`
(confirmed by reading `frontend/openapi/return-platform.openapi.json`), and
`schemaConformance.ts:231` enforces it. So the red measured the **schema gate**,
not the hash — the injection could not distinguish a stable digest from an
unstable one because the body never reached the digest comparison.

Discarding was correct, and recording the reasoning was more correct still:
F7b then landed one layer deeper for exactly that reason, and F7a survives as
positive evidence that the document→mock direction bites.

## 5. F7c — the bound, established and honestly stated

**INJ-F7c reproduced** (`etagFor(body.data)` → `etagFor(body)`), full suite —
6 failed / 861 passed, the four non-pre-existing reds being the 304 test, the
old stability test, and both new tests.

**The bound is:** the envelope's `meta.generated_at` moves at *millisecond*
resolution, so F7c is caught by the pre-existing back-to-back test and
demonstrates nothing about second-or-coarser leakage — which is the only
resolution a ten-second poll exposes. `ACC4.ledger.md:305-312` states exactly
this, quoted and unhedged, and F7b is offered as the proof. **The record is
honest.**

A second bound, also recorded — in `casePanelHandlers.contract.test.ts`'s
`panelBytes` docstring rather than in the audit: the digest is taken over
`body.data` by contract, and `panelBytes()` strips `meta` from the byte
comparison, so no test on this branch can detect a wall-clock leak *inside*
`meta`. That exclusion is correct (`generated_at` must vary) and is declared
rather than assumed.

Only nit: the audit table's `"A, bounded — see above"` points at the F7b
section, which states F7b's bound, not F7c's; the actual statement is in the
ledger the audit names at its top. Traceable. Not a finding.

## 6. Production findings, verified independently

### E1 — AMENDMENT-6 was ruled and never executed. **Sustained, blocking, escalated.**

Verified in all four places by direct reading, not by trusting the document:

| surface | evidence |
| --- | --- |
| DTO | `backend/src/return_platform/operations/case_panel.py:205,206,208` — `support_digest`, `clarifications`, `parked_messages` |
| composer | `backend/src/return_platform/api/case_panel.py:112,113,115` — all three hardcoded empty |
| published OpenAPI | `CasePanelView.properties` contains all three (parsed from `frontend/openapi/return-platform.openapi.json`) |
| frontend mock | `frontend/src/mocks/handlers/casePanelHandlers.ts:216,217,224` |

And the V1 comment AMENDMENT-6 quotes as *"a connection that does not exist"* is
still at `api/case_panel.py:105-111`, word for word.

AMENDMENT-6 (`contracts.md:36`) rules that §9's `CasePanelView` **loses** these
three fields. The live DTO still declares them. Under RV rule 2 — *contract
drift: DTO shapes differing from contracts.md in any respect* — **this is a
blocking finding.** Severity: blocking, and it is not softened by the fields
being empty. The amendment exists precisely because V3 built a section against
`panel.clarifications` that rendered nothing on every real panel while
hand-built tests stayed green; three fields that no registered section can ever
fill remain a standing invitation to repeat that.

**The "measured, not stale" qualifier is the part that makes this stick**, and I
did not take it on trust: I read the DTO itself, which is the generator's input.
The document cannot be stale relative to a source I read directly. (I attempted
`npm run contracts:check` independently; it exceeded a 5-minute budget in the
Python export step and I killed it — the tree was left clean. Recorded as
**not independently re-run**; the DTO reading stands in its place and is
stronger.)

**Ownership:** V1/V3 and the integration agent, not ACC4. ACC4 found it, sized
it correctly, and reported without repairing, which is right. **Escalated to the
orchestrator.** Stated plainly as asked: the amendment was ruled and never
tracked to execution, and it went unnoticed for as long as nobody tried to use
the thing.

### F1 — `npm test` under load. **Finding against this branch.** *(rule 13)*

The dispatch asks which of three things this is. The answer is **all three, and
the branch's record misattributes the one that matters.**

1. **vitest behaviour under resource pressure** — real. `vitest.config.ts` sets
   `pool: "forks"` and **no `maxWorkers`**; on 8 logical CPUs it fans out to the
   default and worker start-up times out. Remedy: cap workers or raise the pool
   timeout.
2. **A reporter artifact** — real. `Test Files 40 passed (40)` takes its
   denominator from files that *started*, so a truncated run reads green in its
   headline. Remedy: none available to this repo; it is upstream cosmetics.
3. **A real gate defect** — real, and **larger than the branch states.**

**Not reproduced here.** Two attempts: unloaded, `npm test` completed in 49.86 s
(62 files / 867 tests); under 12 concurrent CPU-saturating jobs it still
completed, in 85.86 s. The trigger is load-dependent and I could not recreate
the author's conditions. The author's verbatim capture stands as the observation
(`ACC4.ledger.md:71-89`); I record my attempts as **unreproduced**, not as
refutation. The gate consequence below does **not** depend on reproducing it.

**The finding.** `frontend-audit.md:177-179` says:

> the denominator is the files that *started*. The exit code is the only thing
> that saves it (`EXIT=1`).

Repeated at `ACC4.ledger.md:93-94` and in STATUS's finding 6. **It is wrong.**
`.github/workflows/checks.yml:479-489` runs the suite under `set +e` and fails
the step only when the status **exceeds 1**:

```yaml
          set +e
          npm test -- --reporter=default --reporter=junit --outputFile.junit=junit-frontend.xml
          status=$?
          if [ "$status" -gt 1 ]; then
```

Exit 1 is the *tolerated* path by design — it means "tests failed", and the
verdict is handed to `scripts/ci/assert_known_failures.py`. So the safeguard the
branch names does not operate in CI at all.

What actually saves the run today is a **different and contingent** mechanism.
`assert_known_failures.py:106,117-126` fails the job for
`missing = allowed - ran` — an allowlisted test that was not collected. Under
the observed truncation `registry.test.ts` was among the 21 unstarted files, so
its two allowlisted ids landed in `missing` and the job would have failed.

**But that is luck about which files got dropped.** Had the pool dropped 21
files *not* containing `registry.test.ts`, then: `failed` = the two registry
failures ⊆ `allowed`; `repaired` = ∅; `missing` = ∅ → **exit 0**. Nothing in
`checks.yml` or the allowlist asserts a floor on files or tests collected; the
only collapse guard is `if not ran` (line 100), which catches a *total* collapse
and nothing short of it. **`frontend-tests` can therefore report green having
executed two thirds of the suite** — which is the conclusion the branch reaches
in its first sentence and then undercuts with a safeguard that is not there.

This is rule 13 in its own idiom: a guard exists (`assert_known_failures.py`),
and the thing that would have caught this reaches it only by accident.

*Required:* correct the mitigation sentence in `frontend-audit.md`,
`ACC4.ledger.md` and `STATUS.md` finding 6 to state that CI tolerates exit 1 and
that the catch is the allowlist's not-collected rule, contingent on a dropped
file containing an allowlisted test; and name the remedy the analysis actually
supports — a collected-count floor in the gate, alongside the `maxWorkers` cap.
The defect itself is pre-existing and correctly reported-not-repaired; **only
the record needs fixing**, and it is a planning-document edit.

### FE-DEFECT-5 — axe run by no workflow. **Sustained; belongs elsewhere.**

Confirmed: `Select-String -Path .github/workflows/*.yml -Pattern
"playwright|test:e2e|axe"` returns nothing. `checks.yml` has four jobs —
`backend`, `frontend-static`, `frontend-tests`, `contracts` — and none invokes
Playwright. `vitest`'s include is `src/**`, which cannot match
`frontend/tests/canonical-routes.spec.ts`.

This is a genuine **rule 13** finding: a guard with no gate. It is **not this
branch's** — neither the spec nor the workflows were authored or touched here,
and ACC does not own CI. It belongs to whoever owns `checks.yml`. Correctly
raised and correctly not repaired. Carried to the orchestrator alongside E1, not
charged against ACC4.

### FE-DEFECT-2, -4, -6 — accepted as recorded

-2 reproduced on every run of this review and confirmed to survive on trunk
head. -4 verified: the docstring at `TemplateReviewSection.tsx:39` pointed at a
file that did not exist until this commit, and the guarantee genuinely lives in
`CasePanel.test.tsx`; ACC correctly declined to edit production source and
recorded the correction in the new file's header instead. -6 is a defensible
finding measured against the console's own announcer pattern rather than an
invented standard.

## 7. Test integrity — clean

* **No skips, no `.only`, no `.todo`** — `git grep -nE
  "(it|test|describe)\.(only|skip|todo)\(" f4d9743a -- frontend/src/` returns
  nothing, repo-wide at head.
* **No deleted tests** — `git diff --diff-filter=D --name-only` over the range
  is empty.
* **No weakened assertions** — the only change to an existing file is additive:
  one import, one helper, two new `it` blocks. Nothing existing was touched.
* **`scripts/ci/known_test_failures.json` byte-identical to trunk** — no diff
  over the range, and no commit in the range touches it.
* **No production source changed** — confirmed above.
* **No mock standing in for a live-infra acceptance path** — and this is the
  branch's own loudest disclosure rather than something I had to find.

## 8. Rule 12 — frontend outcome gate

Recorded with **outcome evidence, not assertions of consideration**
(`ACC4.ledger.md:587-762`):

| gate | evidence |
| --- | --- |
| token reuse | `review.conflict` is an M3 role pairing with no hex, *measured* at ≥ 7:1 by `reviewContrast.test.ts:297`; the scoping trap in `supportTokens.test.ts` named so a reader does not draw the wrong conclusion |
| accessibility | WCAG 2.1 AA table over the conflict surface with a criterion-by-criterion evidence column, and **INJ-F12** run to prove the keyboard/name-role-value rows are pinned rather than asserted (6 reds) |
| UX copy | three strings judged against §3's list, including the empty-state sentence for a deliberate absence — and the copy is *asserted in the tests*, presence of one wording and absence of the other |
| code review | `engineering:code-review` invoked; **two findings raised against the author's own tests and both acted on** — an overstated justification corrected, and `expect(seeded.status).toBe(200)` added; one finding noted and deliberately not changed, with the blast radius reasoned |

Skill availability: recorded as available, no degradation needed
(`ACC4.ledger.md:589-590`). A separate degradation *is* recorded honestly —
Node `24.14.0` against `.nvmrc`'s `24.18.0`, with CI named as the authority
(`:56-67`). That is the convention working.

Gates named for every guard added (`:759-761`), and I confirmed each: the two
test files run under `frontend-tests`; `lint`/`typecheck` under
`frontend-static`; MSW/OpenAPI conformance under `contracts`.

**Handoff spec:** absent, and I am **not** raising it. Rule 12 scopes the gates
to UI steps; this branch adds no UI, no component and no design, so there is
nothing to hand off. Noted as an observation only.

## 9. What it says it did not reach — recorded as unexecuted, not green

Confirmed against the convention. `frontend-audit.md:276-289` is a dedicated
table headed *"an unexecuted scenario is not a green one"*, closing with
"Nothing above is claimed as green", and it names:

* **the backend serving these guarantees** — flagged by the branch itself as
  "the single largest gap in this document". Everything was measured against the
  MSW contract surface, held to the published OpenAPI by `contracts:check`;
  whether `api/case_panel.py` conforms to the mock's *behaviour* is unverified.
  Correctly stated, and correctly not softened.
* **the panel load test** — "not attempted"; `copilot.case_poll_interval_ms =
  10_000` is stated as **still ungated by measurement**, which matches
  `contracts.md:48` ("V1's shipped poll interval is gated on the ACC load test
  at this volume"). Unexecuted, not green.
* parked-message reprocessing, the two-viewer edit-store scenario,
  `conflict_present`'s participation in the **hash** as opposed to its render,
  a11y beyond the conflict surface, the other ~840 tests, and
  `vite build`/`check:bundle`.

The `conflict_present`-in-the-hash row deserves the credit it takes: the three
new conflict tests drive the marker through a handler override, which proves the
render and the write but **not** the digest, and the branch says so rather than
letting five green tests imply the sixth guarantee.

STATUS's C-table row for 24–25 was **rewritten rather than deleted** and repeats
the unreached list inline. Correct.

---

## Findings

**F1 — `frontend-audit.md:177-179`, `ACC4.ledger.md:93-94`, `STATUS.md`
finding 6. Rule 13.** The stated safeguard for FE-DEFECT-1 ("the exit code is
the only thing that saves it, `EXIT=1`") does not operate:
`.github/workflows/checks.yml:486` tolerates exit 1 by design. The actual catch
is `assert_known_failures.py`'s not-collected rule, which fires only if a
dropped file happens to contain an allowlisted test — and no floor on collected
tests exists anywhere in the gate. As written the record makes a real gate
defect look contained. **Why it matters:** the next owner reads this document to
decide whether CI is protected, and it currently tells them it is. Correct the
three passages and name the remedy the analysis supports (collected-count floor
plus the `maxWorkers` cap). Planning documents only; no test or source change.

## Escalated to the orchestrator — not charged to this branch

**E1 — AMENDMENT-6 unexecuted. Rule 2, contract drift, blocking.** All three
retired fields remain on the DTO (`operations/case_panel.py:205-208`), in the
composer (`api/case_panel.py:112-115`), in the published OpenAPI, and in the
mock. Owned by V1/V3 and the integration agent. Verified independently against
source, not against the audit's report of it.

**E2 — FE-DEFECT-5, the axe sweep no workflow runs. Rule 13.** Pre-existing;
owned by whoever owns `checks.yml`.

---

## Note

The work under review is unusually well constructed, and two things in
particular should survive into the next round. The principal-independence test
is the first one on this run whose non-vacuity I could **demonstrate by
deletion** rather than argue — removing the seed makes it pass over a live
defect, which is exactly the property a vacuity guard should have. And the
branch raised two findings against its own tests in its own code review and
acted on both, including deleting a justification that read like a mechanism and
was not. That is the habit this run has been trying to install.

F1 is a one-paragraph correction to three planning documents. Fix and resubmit;
re-review will cover the complete updated diff.
