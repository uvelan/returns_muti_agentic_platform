# AMEND6 — round 2

**Branch** `feat/amendment-6`, head `3d0ebf125bbe5821ba69613f2c4f36d280b0ee32`
(round 1 was `0e1c43e4`), rebased onto trunk `16868eaa` — confirmed an ancestor
of the head.
**Reviewed** the complete diff `16868eaa..3d0ebf12`, and the round's own commit
`3d0ebf12` in full.

## Verdict: `PASS`

All three findings closed. F2 is closed on an argument I accept rather than on
the edit I suggested, and I say why below. Round 1's code half is not
re-derived; it did not move, and I checked that it did not move rather than
assuming it.

---

## Nothing executable moved — checked before anything else

`3d0ebf12` touches three source files: `supportPanelPayloads.ts` (14),
`supportPanelPayloads.test.ts` (9), `supportSections.test.tsx` (7). Thirty
lines, and **every one of them is inside a `/** */` or `//` block**. No
statement, no expression, no signature, no import, no fixture. The claim is
exact.

Measured on the head, in the `rmap-amend6` worktree with `PYTHONPATH` pinned to
its own `backend/src` (`return_platform.__file__` resolves to
`K:\Projects\Ret\rmap-amend6\backend\src\...`, so the venv's `.pth` back-pointer
to the main worktree is not in play):

| gate | result |
| --- | --- |
| backend pytest | **5256 collected**, `5246 passed, 10 skipped, 514 deselected`, **0 failed** |
| `assert_known_failures.py --suite backend` | *"suite size held: 441 files/modules, 5256 cases (floor 441 / 5251) … 0 failed, 0 allowlisted"* |
| frontend `npm test` | **62 files / 867 cases / 865 passed**, 2 failed — the allowlisted `registry.test.ts` `/shipments` pair |
| `assert_known_failures.py --suite frontend` | *"suite size held … only the 2 known, still-failing tests failed"* |
| `npm run typecheck` | exit 0 |
| `npm run lint` (`--max-warnings=0`) | exit 0 |
| `npm run contracts:check` | exit 0, `CaseFactProjection (11)` verified |

`git diff 16868eaa HEAD -- scripts/ci/` is **empty**: both
`suite_size_floor.json` and `known_test_failures.json` are byte-identical to
trunk. Floor correctly not restaked. Worktree left clean (the JUnit report I
generated for the allowlist gate was removed).

Re-running rather than reasoning was the right call and the figures survive it.

---

## F1 — closed

All four sites are past tense, name the retirement, and **keep the reason the
comment exists**, which is the half that mattered:

- `supportPanelPayloads.ts:499-508` — `hardcodes` → `hardcoded`, plus *"the
  field is now retired — off the DTO, off the composer and out of the published
  document — so there is no longer a line to point at."* That last clause is the
  right thing to say: it tells the reader the pointer is gone on purpose rather
  than rotted. The preserved reasoning — a fallback to a source that cannot have
  a value is a second path that hides the first one failing — is intact and
  still correct.
- `supportPanelPayloads.ts:555-564` — same treatment, and it goes one better:
  *"the fallback is not merely dead but unwritable: `panel.parked_messages` no
  longer type-checks."* True, and it is the `Served<T>` `-?` mapping from round 1
  doing the work, so the comment now points at a mechanism instead of a line
  number.
- `supportPanelPayloads.test.ts:413` and `supportSections.test.tsx:459` — same,
  and both keep the operational point (the entry would never appear on exactly
  the deployments where an operator needs it).

**The sweep is clean, and I ran it rather than reading it.** Across
`backend/src` and `frontend/src`, every surviving mention of the three fields is
past tense or unrelated:

- `operations/case_panel.py:200-201` — *"That sentence used to be false for three
  fields"*. Past tense, correct.
- `clarificationModel.ts:34`, `clarificationModel.test.ts:5,32,40,47` — all
  past-tense or describing the guard.
- `casePanelHandlers.ts:239` — *"There used to be top-level …"*.
- `supportPanelPayloads.ts:76`, `supportHandlers.ts:122` and the two test hits on
  `"support_parked_messages"` — a **section id**, not the DTO field. Not in scope
  and correctly untouched.
- `clarificationModel.ts:135` `payload?.clarifications` — the contributed
  section's payload, which is the migrated read. Correct.

A regex for present-tense assertions (`(field) … is/are hardcoded|declared|
empty|set`) over both trees returns nothing outside ledger prose.

## F2 — closed, and the departure is the better answer

**I agree with the author, and I withdraw the specific-sha half of the
resolution.** Plainly: branch + commit subject satisfies F2.

The requirement I raised was that the record's only pointer to the work must
resolve. A specific sha was one way to get there, not the requirement itself —
my own resolution line already offered "(or the merge commit)" as an
alternative. And the evidence is checkable, so I checked it on the head rather
than taking the ledger's capture:

```
git merge-base --is-ancestor dafd8a07 HEAD  → exit 1
git merge-base --is-ancestor b7e0a529 HEAD  → exit 1
git merge-base --is-ancestor a46e858b HEAD  → exit 0
```

`b7e0a529` was already unreachable when the author read my review — the rebase
onto my own review tip moved it to `a46e858b`, by the same mechanism that had
moved `dafd8a07` to `b7e0a529` a day earlier. Two shas orphaned in a day. A
third would have been orphaned again, with certainty, because this branch
rebases once more before it merges. Complying with the letter of F2 would have
re-armed F2. That is not a dodge; it is the finding's own logic followed one
step further than I followed it.

So: `feat/amendment-6` + *"refactor(panel)!: execute AMENDMENT-6 -- retire the
three unfillable DTO fields"* is a pointer a rebase cannot move, and
`git log --grep` resolves it. Both records now carry the reasoning inline, so
the next reader is not left to rediscover why there is no sha. Withdrawn on
evidence, which is what I asked for.

The figure half is straightforward and correct: both blocks read **5256 / 867**,
with the reason 5256 is not this branch's doing stated inline rather than left
to be reconciled against `suite_size_floor.json`. My independent collect
(`5256/5770 collected`) matches.

## F3 — closed, and the three-state separation holds

Both edits are **insert-only** — `git diff --numstat` on the round's commit
reports `11 0` for `V1-phase2.md` and `18 0` for `V3-frontend.md`. Zero
deletions is a structural proof of the "no wrong sentence edited or removed"
claim, not a claim I have to spot-check. §1 survives verbatim, "**Nothing can
fill it**" included, and the V1 table rows at `:102-103` are untouched beneath
the banner.

**The third state is the one I asked about, and it is not flattened.** The
`V3-frontend.md` banner separates all three explicitly:

1. the diagnosis — *"accepted and acted on"*;
2. `**"What the console does" below is no longer what it does**` — named by its
   own heading, so a reader skimming to `:40` is caught;
3. `**"What is owed on the backend" is still owed and still correct** — no
   production module registers a clarifications section yet.`

And that third claim is true on the head: `PANEL_SECTION_IDS` is still
`Final[tuple[str, ...]] = ()` at `operations/case_panel.py:252`, and every
`register_panel_section` hit in `backend/src` is the definition, its `__all__`
entry, or prose. Had the banner said "superseded" over the whole section it
would have told a future reader the backend work was done — a fresh instance of
the defect the amendment exists to fix, in the amendment's own correction. It
does not.

Break-test 3 is annotated in place at `:229-234`: no longer runnable as written,
the current equivalent named (spread a `clarifications` key onto the panel and
read it back), and its measured result given — *"reds exactly one test, the
retirement guard in `clarificationModel.test.ts`"*. That matches my round-1
injection exactly (1 failed / 13 passed, the failure at `clarificationModel.
test.ts:51`), and the original 3-of-14 measurement is preserved as the evidence
that produced the amendment. The `V1-phase2.md` banner sits between the
`### CasePanelView, frozen` heading and the table, which is where a reader
opening that table for the field inventory will hit it.

---

## Noted, not found

- **The quoted subject uses an em dash where the commit uses `--`.**
  `STATUS.md:329` and `merge.md:119` render the subject as *"execute AMENDMENT-6
  — retire …"*; the commit is `execute AMENDMENT-6 -- retire …`, so
  `git log --grep` on the quoted string verbatim returns nothing (grepping any
  distinctive fragment, as the ledger itself does, resolves it to `a46e858b`).
  Not blocking — the records point at `--grep` as a method rather than offering a
  string to paste, and the recovery is one obvious keystroke, unlike a dangling
  sha. But since the whole point of this citation is that it resolves, and the
  fix is one character in two files, take it on the next rebase.
- **`scripts/dev/ledger_capture.sh`** — unchanged from round 1. Dev-only, no
  gate, ships nothing.
- **The two allowlisted `registry.test.ts` failures** — pre-existing, owned by
  ACC4 E1, untouched.

## Summary

Thirty comment lines, two banners and two corrected records, and every gate
re-measured on the head rather than inherited. The four sentences that asserted
the world this branch changed now describe it in the past tense without losing
the reasoning they existed to carry; the two handoffs keep every wrong sentence
verbatim and lose only their ability to function as instruction; and the
tracking records cite the one thing about the work a rebase cannot move,
because the author noticed that my suggested fix would have failed the same way
twice already had it been taken. Merge permitted.
