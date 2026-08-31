# Items 24–25, audited by fault injection — ACC phase 4

Base `2d0e3d65e4005b32ef30c599fd57c302cd4a0ff9`, branch `feat/acc-frontend`.
Ledger with verbatim commands and output: `.plan/tracks/ACC4.ledger.md`.

STATUS recorded 24–25 as *"outside this dispatch's scope as written — backend
tests only… Needs the frontend suite and a widened brief or a different owner."*
This is that owner. The instrument is ACC3's: for each guarantee, break the
mechanism in `src/`, re-run, record whether the suite reddens. **No backend test
was run at any point on this branch**, live-infrastructure or otherwise.

Every injection was reverted immediately. `git status --porcelain` is empty at
every commit boundary; the only files this branch changes are two test files and
these planning documents.

---

## The headline: two guarantees had no test anywhere, and one of them is the conflict marker

ACC3's dominant finding was **mis-pointed rows** — coverage that existed
somewhere other than where STATUS said. The frontend's dominant finding is
different and worse in one case: for **three** of the eight scenarios the plan
names, the search returned nothing at all.

The conflict-presence surface is the one that matters. `conflict_present`
is a case-scoped versioned marker that §6 says "participates in the shared panel
hash" and is "cleared by the canonical-edit write". Before this branch:

* `grep -rn "conflict_present: true" frontend/src/` returned **one comment and
  no fixture**;
* removing the marker's effect on the Send control (**INJ-F2**) left the suite
  at `858 passed`;
* removing the banner entirely (**INJ-F3**) left the suite at `858 passed`.

**Zero new failures, twice.** Nothing in the frontend watched it.

*Business consequence, at its true size:* two associates open the same case, one
saves an edit, the second sees no banner and a live Send button. The backend
still refuses — §6 rejects unresolved conflicts at the CAS — so **nothing wrong
is sent**. What is lost is the marker's entire purpose: the associate is handed
a bare refusal for a condition the screen existed to warn them about and to
offer a one-press way out of, and presses Send again. This is a coverage defect,
not a behaviour defect: production is correct.

*Closed by* `TemplateReviewSection.test.tsx` (5 tests). Under INJ-F2 two redden;
under INJ-F3 two redden; under **INJ-F10** — the resolve button clearing the
banner *without* issuing the canonical-edit write — exactly one reddens and the
other four stay green, which is the measurement proving that one test owns the
write rather than re-testing the banner.

**It asserts its own premise**, and that is the part built to last. `blocked` is
`gaps.length > 0 || review.conflict_present`; a conflicted fixture that *also*
carried a gap would be blocked either way and every assertion would pass with
the conflict limb deleted — **ACC3's vacuity finding in its frontend form**. So
`expectsConflictOnly` asserts the gaps are empty before it asserts anything
about the block, and asserts the *conflict's* wording ("Settle the other edit
first.") while asserting the *gap's* wording is absent.

---

## The second hole: stability was pinned against the wrong clock

The contract asks for *"two polls with no state change **while a timer ticks** →
identical ETag"*. The existing test, `moves the ETag when the panel moves, and
holds it when nothing does`, issues its two reads **back to back**. It therefore
pins a *millisecond*-resolution wall-clock leak and cannot pin a
second-or-coarser one — which is the only resolution a real ten-second poll
would ever expose.

Measured, not argued. **INJ-F7b** put `Math.floor(Date.now()/1000) % 4` on a
declared field: both stability tests stayed green, and the red came from an
unrelated fixture assertion in `CasePanel.test.tsx` that happens to check "1 of
3 reminders" — a guard that would not exist for any field nobody renders.

*Closed by* `holds the ETag across a real wall-clock second, with the deadline
ticking`, which lets 1.1 s of real time pass and asserts two premises first:
that a deadline is present and in the future (a payload with no timer satisfies
"no wall-clock value" trivially), and that the clock genuinely moved. Under
INJ-F7b it is now the **only** test in the file that reddens.

---

## The third: principal independence had nothing, and the obvious test would have proved nothing

`grep -rni principal` over the test suite returns nine files, **none of them a
panel test**. The trap in writing one is the one the dispatch names: the MSW
handler ignores `Authorization` entirely, so "two principals get identical
bytes" is true of an empty room.

*Closed by* `serves two principals the same bytes and the same ETag, commands
included`, which **approves first** to seed an `accepted_commands` entry — the
field §9 names explicitly as staying unfiltered — and asserts it is present
before comparing. It also asserts the two distinct `Authorization` values from
the **server's own view of the requests**, not from what the test passed, so a
fetch layer that dropped the header cannot turn it into a comparison of one
principal with itself.

**INJ-F11** filters `accepted_commands` by principal. Across all 62 files,
**exactly one test catches it**, and it is this one. Before: none.

---

## Full injection table

Baseline `858 passed / 860` (the 2 failures are FE-DEFECT-2's, pre-existing).

| # | scenario | what was injected | caught? | verdict |
| --- | --- | --- | --- | --- |
| F2 | conflict presence | `blocked` loses its `\|\| conflict_present` limb | ❌ **858 green** | **hole closed** |
| F3 | conflict presence | the conflict banner never renders | ❌ **858 green** | **hole closed** |
| F4 | compose + hash | `If-None-Match` never sent — no conditional read at all | ✅ `casePanel.test.ts` | **A** |
| F5 | cache headers | `Vary: Authorization` dropped from `/panel` | ✅ contract test | **A** |
| F6 | degraded ≠ gap | `isDegraded` returns `false` always | ✅ 2 tests | **A** |
| F7a | hash stability | a computed countdown added to the body | — | **DISCARDED** — red on `additionalProperties: false`; measures the schema gate, not the hash |
| F7b | hash stability | per-second wall clock on a **declared** field | ❌ by both stability tests (caught only incidentally elsewhere) | **hole closed** |
| F7c | hash stability | `etagFor(body)` — hash the envelope's `generated_at` | ✅ both stability tests | **A**, bounded — see above |
| F8 | parked entry | parked `count` forced to 0, so the entry vanishes | ✅ 8 tests | **A** |
| F10 | conflict cleared | "Keep this version" clears locally, never writes the canonical edit | ✅ new test only (4 siblings green) | **A** after strengthening |
| F11 | principal independence | `accepted_commands` filtered by principal | ❌ **before**; ✅ new test only, 1 of 62 files | **hole closed** |
| F12 | a11y / keyboard | `aria-disabled` + `aria-describedby` → `disabled` | ✅ 4 tests | **A** |
| F13 | stale source vs 304 | the digest ignores contributed `sections`, so a stale source cannot move the ETag | ✅ 3 tests | **A** |

**Discarded: 1** (F7a) — aimed at a line that reads like the mechanism rather
than the one that is it. Recorded because the reasoning is the finding, and
because F7b then landed one layer deeper for the same reason.

**Tests added: 7**, each injected against, each asserting its own premise.
**Before: 61 files / 860 tests / 858 passed / 2 failed.**
**After: 62 files / 867 tests / 865 passed / 2 failed.**

---

## Where these guarantees actually live — the falsifiable map

Following ACC3's convention: a row naming a *file* asks for trust; a row naming
*the line to delete and the test that goes red* is checkable in a minute.
**Every line below was executed.** A guarantee absent from this table is
**unverified, never "fine"**.

| guarantee | delete this | this reddens |
| --- | --- | --- |
| the conditional read happens at all | `casePanel.ts:158` `If-None-Match` | `revalidates with the ETag it holds and answers from the cache on 304` |
| `private, no-cache` + `Vary` | the `PANEL_HEADERS` `Vary` entry | `declares the cache headers the contract fixes, on both surfaces` |
| ETag holds across a millisecond | `etagFor(body.data)` → `etagFor(body)` | both stability tests |
| ETag holds across a **second** | a per-second value on any declared field | `holds the ETag across a real wall-clock second…` — **this one only** |
| two principals, one body and one ETag | filter `accepted_commands` by principal | `serves two principals the same bytes and the same ETag…` — **this one only, 1 of 62 files** |
| a stale contributing source is not masked by a 304 | drop `sections` from the digest input | 3 tests in `support/supportPanelIntegration.test.tsx` — **not** a panel or contract file |
| degraded is not empty | `isDegraded` → `false` | `supportPanelPayloads.test.ts`, `supportSections.test.tsx` |
| the parked entry is visible | parked `count` → 0 | 8 tests across 4 files |
| a conflict blocks Send, and says why | `blocked`'s `\|\| conflict_present` limb | `blocks it, and names the conflict as the reason…` |
| a conflict is on the screen | the banner's `conflict_present` guard | `is on the screen, in words about the other person…` |
| clearing it is the canonical-edit **write** | the `resolveEdit` call behind "Keep this version" | `is done by the canonical-edit write, and the panel then unblocks` |
| a blocked Send stays keyboard-discoverable | `aria-disabled` → `disabled` | `keeps a blocked Send focusable and says why` + 3 conflict tests |

Two rows a reader would guess wrong, in ACC3's mis-pointed sense: the **cache
headers** are pinned in the *mock contract* file and not in `casePanel.test.ts`
where the client lives, and the **stale-source** guarantee is pinned in the
*support integration* file rather than anywhere named "panel".

---

## Production findings, reported not repaired

### FE-DEFECT-1 — `npm test` as written does not complete on a loaded machine

```
Error: [vitest-pool]: Failed to start forks worker for test files …
Caused by: Error: [vitest-pool-runner]: Timeout waiting for worker to respond
…
 Test Files  40 passed (40)
      Tests  438 passed (438)
     Errors  21 errors
   Duration  310.06s
```

**21 of 61 files never started**, and the headline reads `40 passed (40)` because
the denominator is the files that *started*.

This is **three** defects wearing one coat, and an earlier draft of this
document got the third badly wrong — it said *"the exit code is the only thing
that saves it (`EXIT=1`)"*. That is false, and correcting it makes the finding
**larger**. (RV finding ACC4-1 F1.)

**(a) Vitest pool behaviour under resource pressure.** `vitest.config.ts` sets
`pool: "forks"` and **no `maxWorkers`**, so it fans out to the default and
worker start-up times out. Capping at 2 makes the suite both complete **and four
times faster** (79 s vs 310 s).

**(b) A reporter artifact.** `Test Files 40 passed (40)` takes its denominator
from the files that *started*, so a truncated run reads green in its headline.
Nothing in the summary says 21 files were expected and are absent. No remedy
available in this repository; it is upstream cosmetics.

**(c) A real gate hole — and the exit code is not a safeguard.**
`.github/workflows/checks.yml:479-489` runs the suite under `set +e` and fails
the step only when the status **exceeds 1**:

```yaml
          set +e
          npm test -- --reporter=default --reporter=junit --outputFile.junit=junit-frontend.xml
          status=$?
          if [ "$status" -gt 1 ]; then
            echo "::error::vitest exited $status -- the run failed, not the tests"
            exit "$status"
          fi
```

**Exit 1 is the tolerated path by design** — it has to be, because this suite
legitimately exits 1 on its allowlisted failures (FE-DEFECT-2's two). A
truncated run exits 1 and walks straight through. The safeguard the earlier
draft named does not operate in CI at all.

What actually caught the observed run is a **different and contingent**
mechanism: `scripts/ci/assert_known_failures.py` computes three sets against the
allowlist,

```python
unexpected = sorted(failed - allowed)
repaired   = sorted(allowed & (ran - failed))
missing    = sorted(allowed - ran)
```

and dropped files produce neither failures nor passes, so `unexpected` and
`repaired` are both empty. The only rule that can fire is `missing` — *an
allowlisted test was not collected*. The step runs
`assert_known_failures.py --suite frontend`, and that suite's allowlist in
`scripts/ci/known_test_failures.json` (`suites.frontend.known_failures`) holds
exactly two entries, **both in the same file**:

```
src/domains/registry.test.ts::the domain registry > declares exactly the canonical domains
src/domains/registry.test.ts::the domain registry > shares a visibility capability only where that is deliberate
```

`registry.test.ts` **happened** to be among the 21 dropped files, so its two ids
landed in `missing` and the job would have failed. **That is luck about which
files got dropped.** Drop 21 files containing no allowlisted test and:
`failed` ⊆ `allowed`, `repaired` = ∅, `missing` = ∅ → **exit 0, having run a
third of the suite.** The script's only other floor is `if not ran`, which
catches a *total* collapse and nothing short of it.

Stated as the general property, because that is what a fix has to answer:
**a comparator built from an allowlist can only notice failures already on its
list.** It is the right instrument for "did anything new break" and structurally
the wrong one for "did the suite actually run" — and nothing in `checks.yml`
asserts a floor on files or tests collected. Rule 13 in its own idiom: the guard
exists, and the thing that would have caught this reaches it only by accident.

*The remedy this analysis supports*, for whoever builds it: **a collected-count
floor in the gate** — assert a minimum number of files/tests in the JUnit report,
or compare the collected set against a committed manifest — **alongside** the
`maxWorkers` cap, which addresses (a) but not (c). The cap alone would make the
truncation rarer without making it visible.

**Reproduction status: unreproduced, not refuted.** RV attempted it twice and
could not: **49.86 s** complete when unloaded (62 files / 867 tests), and
**85.86 s** complete under twelve concurrent CPU-saturating jobs. The capture
above stands as the observation — it happened, verbatim, in
`.plan/tracks/ACC4.ledger.md` step:01 — and its trigger threshold is unknown.
**The gate hole stands independently of anyone reproducing the truncation**,
because it is visible by *reading* `checks.yml` and `assert_known_failures.py`
rather than by inducing the condition. That is the part that matters to whoever
builds the floor guard.

*Context, not causation:* the machine was loaded because another agent was
running the live suite — the contention under investigation.

### FE-DEFECT-2 — the merge tip is red in the frontend suite

`src/domains/registry.test.ts`, 2 tests, on a clean tree at the base commit:

```
- Expected
+ Received
    "/returns",
+   "/shipments",
    "/support",
```

`14aa6915` registered a `/shipments` domain and never updated the registry test.
It is an ancestor of the integration tip, so **`frontend-tests` is red on trunk**
and every reading in this document is taken against an already-failing suite —
which is why the 2 are subtracted explicitly everywhere rather than absorbed.

Same class as ACC3's finding: production moved, its harness did not.

### FE-DEFECT-3 — AMENDMENT-6 was ruled and never executed

AMENDMENT-6 (`22e1aca6`) retires `support_digest`, `clarifications` and
`parked_messages` from `CasePanelView`, because a registered section returns a
`PanelSectionView | None` and **cannot write a top-level field**. All three are
still present in all four places: the DTO
(`operations/case_panel.py:205-208`), the composer that hardcodes them empty
(`api/case_panel.py:112-115`), the published OpenAPI, and the frontend mock.

```
$ git log --oneline -S'support_digest' -- backend/src/return_platform/operations/case_panel.py
32e92df5 (V1) step:15 the panel, the endpoints, and four ways the draft was silently broken
```

One commit ever touched them — the one that added them. And the V1 comment
AMENDMENT-6 quotes as *"a connection that does not exist"* is still in
`api/case_panel.py:105-111` word for word.

**This is a measured fact, not a suspicion about a stale document**:
`npm run contracts:check` passes, including its `git diff --exit-code`, so the
committed OpenAPI is byte-identical to what the backend generates today.

*Consequence:* a frozen-clause retirement is unlanded, and the three fields are
a live invitation for a future contributor to try to fill one — which is exactly
what AMENDMENT-6 was written to prevent, after V3 built a section against
`panel.clarifications` that rendered nothing on every real panel.

### FE-DEFECT-4 — a source docstring points at a test file that does not exist

`TemplateReviewSection.tsx:39` says *"`TemplateReviewSection.test.tsx` asserts
that a field value containing a tag renders as the literal characters"*. Until
this branch **that file did not exist**. The guarantee is real and well pinned —
in `CasePanel.test.tsx`'s *"renders a support-derived value as text, never as
markup"* — so this is ACC3's mis-pointed-row failure one level deeper, in
production source rather than in a status table, and it points at nothing at
all rather than at the wrong thing.

ACC does not edit production source, so the docstring stands. The new
`TemplateReviewSection.test.tsx` records the correction in its own header.

### FE-DEFECT-5 — the a11y sweep item 24–25 asks for is gated by nothing

The repository's only axe run is `frontend/tests/canonical-routes.spec.ts`, a
Playwright spec.

```
$ grep -rn "playwright\|test:e2e" .github/workflows/*.yml
$ (no output)
```

No workflow runs Playwright, and `vitest`'s `include` is `src/**`, which does
not match `tests/*.spec.ts`. `reviewContrast.test.ts` already states this for
its own token and reasons correctly from it; the grep generalises it — **the
sweep is not executed on any push.** Same shape as STATUS's "a guard with no
gate", in the accessibility plane.

What *is* gated is better than it looks: contrast is measured off token strings
against the real palette by `reviewContrast.test.ts` (`review.conflict` ≥ 7:1,
against AA's 4.5:1), and that file exists because the same token shipped at
**1.29:1** and was caught by an audit rather than by a gate.

### FE-DEFECT-6 — a conflict arriving mid-draft is never announced

The banner appears on a background poll and carries no `role="status"` or
`aria-live`. Not measured against an outside standard but against **this
console's own established pattern**: `supportSections.tsx` has a deliberate
announcer for "whoever is not looking at the screen", and its signature is
`artifacts|unbound|parked` — with no conflict term. A screen-reader associate
typing into a draft learns about the other person's edit only when Send refuses.

---

## What was NOT reached — an unexecuted scenario is not a green one

| scenario | why |
| --- | --- |
| **the `/panel` and `/edit-state` guarantees as the *backend* serves them** | **not executed.** Everything above is measured against the MSW handlers, which are the frontend's contract surface and are held to the published OpenAPI by `contracts:check`. The 304, the ETag derivation, `Vary`, `no-store` and principal independence are ultimately implemented in `backend/src/return_platform/api/case_panel.py`, and **no backend test was run on this branch.** The mock conforms to the document; whether the server conforms to the mock's *behaviour* is unverified here. This is the single largest gap in this document. |
| **parked messages reprocessed in stream order on an `nl_enabled` flip** | **not reached.** The frontend's share is the copy — *"will be read in the order they came in"* — which is rendered and asserted. The reprocessing it promises is §5 backend behaviour and is not reachable from `npm test`. |
| **the panel load test at contracts.md's stated volume** | **not attempted.** The plan's item 24–25 line includes *"panel load test at the contracts.md stated volume gates the shipped poll interval"* (≤ 200 concurrent cases, ≤ 20 compositions/sec). It needs the API surface up. `PANEL_POLL_INTERVAL_MS = 10_000` is therefore **still ungated by measurement**, on this branch as on every previous one. |
| **`typing in the edit store does not invalidate other viewers`** | **not reached.** The plan names it; the MSW edit store has one actor (`ACTOR = "associate-mock"`) and no seam for a second, so a two-viewer scenario cannot be built without editing another slice's mock. |
| **`conflict_present` participating in the shared panel hash** | **not reached.** §6 says the marker participates in the hash. The mock's store initialises it `false` and never sets it `true`, so the ETag's response to the marker moving cannot be observed. The three tests added here drive the marker through a *handler override*, which proves the rendering and the write but not the digest. |
| **a11y beyond the conflict surface** | **partial.** WCAG 2.1 AA audited on the conflict path only (contrast, keyboard, name/role/value, error identification). The rest of the panel is covered by the pre-existing keyboard-path tests, which ACC4 did **not** inject against except through INJ-F12. |
| **the other ~840 frontend tests** | **not audited.** Only the item 24–25 surfaces were injected against. |
| **`vite build` / `check:bundle`** | **not run.** `lint` and `typecheck` were; the bundle ratchet was not, and this branch adds no bundled code. |

Nothing above is claimed as green.
