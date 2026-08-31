# AMEND6 — round 1

**Branch** `feat/amendment-6`, head `0e1c43e49909be3f9823b2409802e9a98c50be1b`
**Base** `72f37ba2` (verified an ancestor of trunk `bf7fa140`; trunk's only
commits beyond the base are three review documents, no code)
**Reviewed** the complete diff `72f37ba2..0e1c43e4`, 19 files.

## Verdict: `CHANGES_REQUIRED`

Three findings, all in the documentation plane, none of them behavioural. The
code half of this branch is correct and I could not break it — I tried the two
ways that would have mattered and both held. What fails it is that the branch
argued, in its own commit message, that *a record still asserting the old state
is the defect*, and then left four such records standing: two in a production
source file it did not open, and two figures in the very tracking documents it
opened specifically to correct. That is not a different standard applied to the
author; it is the author's own standard applied to the whole diff.

Re-review will be short. Nothing below requires touching code that runs.

---

## What was verified, and how

### 1. The amendment was still justified at execution time — **confirmed**

This was the check that could have made the whole branch wrong, and each of the
three claims holds on trunk `bf7fa140`, checked by me and not read from the
ledger:

- **Nine `register_panel_section` calls, all in one test file.** Trunk:
  `backend/tests/api/test_case_panel_and_reviews.py` lines 513, 539, 562, 563,
  581, 598, 600, 1478, 1494. Every other hit in `backend/` is the definition
  (`operations/case_panel.py:242`), its `__all__` entry, or prose.
- **No production module registers a section at all.** Same grep; the
  production hits are the definition and comments. Nothing imports it.
- **`PANEL_SECTION_IDS` is empty** — `operations/case_panel.py:239`,
  `Final[tuple[str, ...]] = ()`.
- **The single `CasePanelView(...)` construction hardcoded all three** —
  `api/case_panel.py:112-115` on trunk: `support_digest=()`,
  `clarifications=()`, `parked_messages=0`.

So none of the three had acquired a writer since the amendment was raised, and
retiring all three rather than some was the correct execution. The check was the
right one to insist on and the answer is clean.

### 2. The consumer enumeration is complete — **confirmed, and provable more strongly than by grep**

Greps across `backend/`, `frontend/src`, mocks and fixtures agree with the
author's enumeration exactly:

- **One production reader**, `clarificationModel.ts:132`
  (`for (const raw of [...fromSection, ...panel.clarifications])`), migrated.
- **Zero readers** for `support_digest` and `parked_messages`. Their only
  non-fixture, non-test mentions are the comments in
  `support/supportPanelPayloads.ts` recording that a fallback was deliberately
  *not* written — see finding F1, which is about those comments' accuracy, not
  their existence.
- **No backend test** references any of the three; `clarifications` does not
  appear in `test_case_panel_and_reviews.py` at all. The other `clarifications`
  hits in `backend/src` are `graph_schema_analyzer` and the order agent —
  unrelated domains, not this DTO.

The dispatch's warning that `Served<T>` makes some omissions type-invisible is
worth answering directly, because it cuts the other way here.
`frontend/src/api/casePanel.ts:33-39` maps every property with `-?`, so
`CasePanelView` is a fully-required object type derived from the generated
document. Once the three properties leave the schema, **any** remaining
`panel.support_digest` / `.clarifications` / `.parked_messages` is a hard `tsc`
error, not a silent `undefined`. `npm run typecheck` passes clean on the branch
head, which is a completeness proof for property-access readers that no grep
gives you. The residual gap — a dynamic key or an `unknown` cast — I closed by
grep, and it is empty. Enumeration accepted.

### 3. No test deleted; the re-pointing is honest; the guard is real — **confirmed by injection**

`clarificationModel.test.ts` holds **14 `it(` blocks at the base and 14 at the
head**. Three were re-pointed, none removed:

| was | is |
| --- | --- |
| "still reads the DTO field, in case the integration pass wires that instead" | "draws nothing from a top-level clarifications field, retired by AMENDMENT-6" |
| "draws one card when both vehicles carry the same clarification" | "draws one card when the section names the same clarification twice" |
| "keeps the section's order and appends what only the field holds" | "keeps the section's own order, and the first mention fixes an id's place" |

The other five test files lost only fixture keys from hand-built panel objects.
No `.skip`, `.todo`, `.only`, `xfail` or `pytest.mark.skip` is added anywhere in
the diff.

**The inverted guard was tested, not read.** I restored the retired read into
`readClarifications` —

```ts
const stale: readonly unknown[] =
  (panel as unknown as { clarifications?: readonly unknown[] }).clarifications ?? [];
...
for (const raw of [...fromSection, ...stale]) {
```

— and the suite went **1 failed | 13 passed (14)**, the single failure being
`clarificationModel.test.ts:51`, the retirement guard, on
`expect(readClarifications(stale, undefined)).toEqual([])`. Reverted; worktree
clean. The guard fails for exactly the reason it claims to exist and for no
other, and the author's account of why `panel` stays in the signature unread is
therefore accurate rather than a cover for dead code. `npm run lint`
(`--max-warnings=0`) passes with the `void panel;`, so the parameter survives
the static gate too.

This is the strongest thing on the branch. A retired field with no watcher on
the console side would have been a silent invitation to re-add the read the next
time somebody's section drew nothing.

### 4. The regeneration diff contains only what it should — **confirmed**

All four committed copies are byte-identical (`md5 0c403643…`), and
`scripts/check_openapi_drift.py` returns `status: PASS`, `diffs: []` on the
branch head, covering all four snapshots **and** the generated `.d.ts`. The
content of the change, read in full rather than trusted:

- `CasePanelView.description` — the replacement prose.
- Removal of exactly three property blocks: `clarifications`, `parked_messages`,
  `support_digest`.

Nothing else moved in 877 KB of document across six regenerated artifacts. The
dispatch is right that an unrelated drift riding along here is what nobody would
notice; there isn't one.

### 5. Suites and floor — **confirmed, including the attribution**

Measured by me on the branch head, with `PYTHONPATH` pinned to this worktree:

- **Backend** `5256 collected, 5246 passed, 10 skipped, 514 deselected,
  0 failed`. `assert_known_failures.py --suite backend`: *"suite size held: 441
  test files/modules, 5256 test cases (floor 441 / 5251) … 5256 tests ran, 0
  failed, 0 allowlisted"*.
- **Frontend** `62 files / 867 cases / 865 passed`, the two failures being the
  allowlisted `src/domains/registry.test.ts` pair.
  `assert_known_failures.py --suite frontend`: *"suite size held … only the 2
  known, still-failing tests failed"*.

**The attribution stands, and it is checkable rather than plausible.** This
branch touches zero files under `backend/tests`, so its backend collection delta
is structurally zero, not merely measured zero. The 5251→5256 rise is entirely
`git diff c8eac86d 72f37ba2 -- backend/tests`, which adds precisely five test
functions to `test_cumulative_support_outcomes.py`:
`test_a_new_execution_asks_the_draft_activity_for_the_typed_shape`,
`test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return`,
`test_a_new_execution_consults_the_review_gate_before_it_sends`,
`test_an_unmarked_history_never_reaches_the_review_gate_at_all`,
`test_a_patch_marker_this_module_does_not_know_about_fails_loudly`. Five, from
the `_Runtime` merge. Frontend collection delta is zero for the same reason:
14 → 14 in the only file whose count could have moved.

`scripts/ci/suite_size_floor.json` and `scripts/ci/known_test_failures.json` are
**byte-identical to trunk** (`git diff 72f37ba2 0e1c43e4 -- scripts/ci/` is
empty). Correctly untouched: the floor is a floor, a rise above it is legal, and
5256 is far inside the 25% re-stake ceiling. No floor was quietly lowered to
accommodate a removal — nothing was removed to accommodate.

### 6. Rule 13 — **confirmed, and the last claim is the strongest one**

Every guard this branch adds names a gate that runs it, and each gate exists in
`.github/workflows/checks.yml`:

| guard | gate |
| --- | --- |
| the retirement guard in `clarificationModel.test.ts` | **frontend suite** (`npm test`, then `assert_known_failures.py --suite frontend`) — proved red by injection above |
| the migrated reader (`readClarifications`) | **frontend lint, typecheck and bundle size** — `tsc -b` would reject any restored property access, since `Served<T>` marks it `-?` |
| the regenerated document, all four copies + `.d.ts` | **contract drift** (`npm run contracts:check`, `check_openapi_drift.py`) |

**The `additionalProperties: false` claim is true, and I verified it the only
way worth verifying it.** I added `clarifications: []` back to `panelBody()` in
`frontend/src/mocks/handlers/casePanelHandlers.ts` and ran
`casePanelHandlers.contract.test.ts`: **1 failed | 19 passed (20)**, the
violation reported as `$.data.clarifications` against the published
`CasePanelView`. Reverted; worktree clean.

That is a genuine upgrade in kind, not degree. Before the amendment, the mock
carried the three keys and a *comment* explaining that filling them would be
mocking a path production cannot take — a comment is exactly what nobody reads
before adding a line. Now the schema itself refuses the key. The mechanism that
caught audit finding #14 is doing the enforcement instead of the prose, which is
the whole thesis of rule 13 satisfied in one line of diff.

### 7. Test integrity and frozen modules — **clean**

No skips, xfails, `.only`, weakened assertions or deleted tests in the diff. No
new imports of `operations/associate_flow`, `agents/order_discovery`,
`api/associate_returns` or `api/return_agents` — the grep over the whole diff is
empty. `known_test_failures.json` byte-identical to trunk. No fact-name string
literals introduced. `contracts.md` was not edited and did not need to be: §9
line 111 already read *"per AMENDMENT-6, `support_digest`, `clarifications` and
`parked_messages` are retired"*, so the contract led and the code has now caught
up — which is the correct direction and worth saying out loud.

`scripts/dev/ledger_capture.sh` is new and outside the amendment's subject
matter. I am not raising it: it is a dev-only capture helper, it runs in no gate,
it changes no shipped behaviour, and its existence is a direct response to a
prior review round about transcribed versus captured ledger output. Noted, not
found.

---

## Findings

### F1 — `supportPanelPayloads.ts` still tells the reader the retired fields exist

**File** `frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.ts:502`
and `:557-558`. Also, same claim, in
`support/supportPanelPayloads.test.ts:413-414` and
`support/supportSections.test.tsx:459`.

**Rule** RV blocking rule 2, in the plane the amendment itself was raised over —
a record describing a connection that does not exist. Also the
`engineering:code-review` dimension the branch is otherwise strongest on.

The two live docstrings say, in the present tense:

> `api/case_panel.py` hardcodes `support_digest=()`

> `CasePanelView.parked_messages` is hardcoded `0` and no contributor can change
> it

Both are now false in a way that is worse than being merely out of date.
`api/case_panel.py` does not set `support_digest` at all any more, and
`CasePanelView.parked_messages` does not exist. A reader of `readDigestPayload`
or `readParkedPayload` who asks the obvious question — *why is there no
fallback?* — is handed a reference to a line that will not be there, and cannot
tell from the comment whether the code moved or the comment rotted.

**Why it matters and is not taste.** The whole content of AMENDMENT-6 is that a
sentence asserting a connection the seam cannot make is a defect, not a
documentation nit — V1's comment at `api/case_panel.py:105-111` was *correct in
intent* and wrong in fact, and that is what cost V3 a build. These four are the
same shape, in the same directory, about the same three fields, and the branch
that exists to retire that shape walked past them. The harm is bounded — `tsc`
now stops anyone acting on the comment — but "the type system will catch the
reader who believes our comment" is not a reason to leave the comment.

**Resolution.** Put the two sentences in the past tense and name the retirement,
as the same author already did well in `clarificationModel.ts:30-42`. Two lines
in a production file, two in tests. Note that
`supportPanelPayloads.test.ts:387` already says *"is retired (AMENDMENT-6)"* —
the correct form exists 26 lines above one of the incorrect ones.

### F2 — the corrected tracking records cite a commit that is not on the branch, and a superseded suite figure

**File** `.plan/acceptance/STATUS.md` (finding 5, the `**CLOSED**` block) and
`.plan/merge.md` (the `**EXECUTED**` block).

**Rule** RV blocking rule 10's neighbour — evidence integrity — and the branch's
own stated standard in commit `ef02764c`.

Two defects, both introduced by commit `ef02764c` and both left uncorrected by
the rebase commit `0e1c43e4`, which appended 95 lines to the ledger and did not
revisit the two documents:

1. **`dafd8a07` is unreachable from the branch head.** It is the pre-rebase
   version of what is now `b7e0a529`; `git merge-base --is-ancestor dafd8a07
   0e1c43e4` is false. Both records cite it as *the* commit that executed the
   amendment. It resolves today only because the reflog has not expired.
2. **"Suite sizes unchanged (backend 5251, frontend 867)"** is superseded by the
   branch's own final measurement, `5256`, recorded in the message of the very
   next commit. The *claim* being made — that this branch's delta is zero — is
   true and I verified it; the *number* offered as its evidence is the old one.

**Why it matters.** The dispatch that commissioned this review notes that three
reviews were stranded off trunk today and had to be recovered by sha. A tracking
record whose only pointer to the work is a dangling commit is that failure mode
pre-loaded. And a record that says 5251 where the suite says 5256 will send the
next reader to `suite_size_floor.json` to reconcile a discrepancy that is not
there. `ef02764c`'s own reasoning applies without modification: *"a tracking
record wrong … is indistinguishable from work genuinely remaining."*

**Resolution.** Update both blocks to `b7e0a529` (or the merge commit) and to
`backend 5256 / frontend 867`, with a word on why 5256 is not this branch's
doing — the ledger's Step 8 already has the sentence.

### F3 — the two handoffs need a pointer, not a rewrite

**File** `.plan/handoffs/V1-phase2.md:91-92` and
`.plan/handoffs/V3-frontend.md:15-18, 29, 215-216`.

This is the judgement the dispatch asked me to record either way, and I am
recording it as a finding because I land partly against the author.

**Where I agree.** Rewriting the wrong sentences out of those documents would be
wrong, and the author's reason is the right reason. `V3-frontend.md` §1 is the
document in which somebody *noticed* — it says "**Nothing can fill it**" and
enumerates why — and it is the direct provenance of AMENDMENT-6. Erasing or
softening it would delete the only first-hand account of how a T0 freeze went
wrong, on a run whose amendments are mostly discovered this way. Keep the words.

**Where I disagree.** "History" and "guidance" are not properties of a document;
they are properties of a passage, and these two documents contain both.

- `V1-phase2.md:85-96` is a table headed **"`CasePanelView`, frozen"**, one row
  per field, owner and note. That is not a narrative of what was believed on the
  day — it is the field inventory a later slice opens *to find out what the DTO
  has*. Its `support_digest[]`, `parked_messages`, `clarifications[]` rows are
  now wrong twice over: the mechanism claim was always false, and the fields are
  gone. It reads as current specification because it is formatted as current
  specification.
- `V3-frontend.md:29` states **"What the console does"** — section payload
  first, `panel.clarifications` second, de-duplicated — which is no longer what
  the console does. `:39-43` still instructs a future backend author to add a
  `register_panel_section("clarifications", …)` call that remains unwritten,
  which is *live, correct, outstanding work*. And `:215-216` offers, as a
  reviewer's break-test, "point `readClarifications` at `panel.clarifications`
  alone" — an instruction that no longer type-checks. A reviewer who tries it
  concludes the document is stale and stops trusting the other four items on
  that list, which are all still good.

**Why "§1a already governs" does not close it.** It governs for a reader who
already knows to go there. The reader this matters to is the one who opens
`V1-phase2.md` *because it is the frozen-DTO reference* and never reaches
`contracts.md` §1a — precisely the reader V3 was, and precisely how this defect
propagated the first time. The amendment's own lesson is that a correct record
elsewhere does not neutralise a wrong record in the place people actually look.

**Resolution — and it is small.** Not a rewrite: a dated one-line banner at the
head of each affected passage, e.g. *"⚠ 2026-08-31: superseded by AMENDMENT-6 —
these three fields are retired from `CasePanelView`. The text below is preserved
as the record of what was believed at the time."* The wrong sentences survive
verbatim, so nothing the history argument protects is lost; what is lost is
their ability to function as instruction. The author already applied exactly
this treatment to `STATUS.md` and `merge.md` — the operative distinction on this
branch is therefore not history-versus-guidance, it is documents-I-opened versus
documents-I-didn't, and that is the one distinction that has no principle behind
it.

---

## Not findings

- **`scripts/dev/ledger_capture.sh`.** New, dev-only, invoked by no gate, ships
  nothing. Outside the amendment's subject but harmless and well-motivated.
- **The two allowlisted `registry.test.ts` failures.** Pre-existing, allowlisted,
  owned elsewhere (ACC4 E1). Untouched by this branch, correctly.
- **`.plan/reviews/ACC4-1.md` not being on trunk when the branch read it.** The
  ledger's "Open / not closed" flags it honestly and declines to fix it on
  ownership grounds, which is right — `.plan/reviews/` is mine. It is on trunk
  now (`bf7fa140`), so this is closed by someone else's commit, not by silence.
- **`npm test` exiting 0 with two failures present.** Observed during my run;
  it belongs to the frontend-suite gate's own design, not to this diff, and
  `assert_known_failures.py` catches what matters either way.

---

## Summary

The hard checks all pass. The amendment was still justified when it was
executed, and that was proved rather than assumed. The consumer enumeration is
complete, and `Served<T>` turns out to make it provably complete rather than
type-invisibly incomplete. No test was deleted, and the guard that replaced the
retired one is real — I broke it on purpose and it broke. The regeneration is
exactly three properties and a docstring across six artifacts with zero drift
riding along. The floor is correctly untouched and the five-test rise is
somebody else's, traceable by name. And `additionalProperties: false` now
enforces at the schema what used to be enforced by a comment, which is the best
thing on the branch.

What stops it is that four sentences and two figures still assert the world this
branch just changed — including in the two documents the branch opened
specifically to stop that happening, and in a production file 30 lines from a
comment that already gets it right. Fix those and this is a `PASS`; none of it
touches code that runs.
