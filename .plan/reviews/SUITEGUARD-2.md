# SUITEGUARD — round 2

**Branch** `feat/suite-size-guard` @ `4ceacbc1ccc0a8b102b206b59ee168ca8b1ebd05`
(round 1 was `579bef8e`). Worktree `K:/Projects/Ret/rmap-suite-size-guard`.

**Verdict: PASS** — all three findings closed, nothing regressed, no new findings.

Round 1's substance is not re-derived here. This round covers only the three fixes,
the regression surface, and the one judgement I was asked to record.

No backend suite and no live-infrastructure test was run. Everything below is the
standalone control script, the saved JUnit artifacts, and mutations applied to a
**copy** of the comparator in scratchpad — the worktree was never modified and is clean.

---

## Finding 1 — closed, and I re-applied my own mutation to prove it

The round-2 diff touches no production source: `assert_known_failures.py` is
unchanged since `579bef8e`. The fix is a control, which is the right shape — the
ordering was already correct, it was only untested.

The new control (`size-short-drops-allowlisted.xml`) is aimed at the case that
actually separates the two orderings: `KNOWN_FRONTEND` still failing and
`KNOWN_BACKEND`'s file dropped entirely, so `missing` is non-empty **and** the run
is short. It asserts exit 2, that the allowlist had a verdict to give
(`"was not collected"`), and that the size check overrode it (`"THE SUITE SHRANK"`).

I re-applied my round-1 M5 verbatim — swapping the `if size != 0: return size`
block below `if unexpected or repaired or missing: return 1`:

```
M5 size check demoted below allowlist verdict | exit 1 | 1 control red
  [FAIL] a short run that also lost an allowlisted test exits 2, not 1
```

**Dead, and killed by exactly one control — the new one.** In round 1 this mutation
survived all 35. The precedence is now executed rather than argued, and the kill is
specific: no other control moved, which is what tells you the new control is
carrying it rather than incidental coverage elsewhere.

## Finding 2 — closed, and the reason-assertions are demonstrably what carry it

The branch's mutation table is accurate. I ran all three of its floor mutations
against the fixed controls:

| mutation | red controls | which ones |
|---|---|---|
| `baseline <= 0` clause deleted | 2 | `(via the unusable-floor guard, not the restake branch)`, `(and NOT via restake)` |
| `isinstance(baseline, bool)` clause deleted | 1 | `(booleans are ints in Python; the guard says so)` |
| `count < baseline` → `count < baseline * 0.5` | 11 | incl. every `code == 2` control on the shrink path |

The load-bearing detail is confirmed exactly as claimed. Under the first two
mutations the parent `code == 2` assertions — `rejects a floor of zero` and
`rejects a boolean floor` — **stay green**. The deleted guard is replaced by the
restake branch answering 2 with `the floor has fallen behind: 50 test cases ran
against a recorded floor of 0` (and `... of True`). Only the message assertions
notice.

So the generalisation holds and is worth stating in its strongest form:
**an exit-code assertion cannot distinguish a guard firing from a different guard
firing.** Wherever two branches of a check can return the same code, the code is
not the observable — the reason is. That is the round's most transferable result
and it is now demonstrated rather than argued.

The third mutation is the useful counterweight and I checked it for that reason: a
genuine behavioural change *does* redden the `code == 2` assertions, eleven of them.
The lesson is not "exit codes are weak assertions"; it is "an exit code is ambiguous
exactly where more than one branch can produce it".

## Finding 3 — closed; the corrected text is accurate, and the replacement argument is sound

Checked against trunk rather than against the diff. `known_test_failures.json` on
`refactor/unified-return-platform` holds **one** backend id
(`tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item`)
and **two** frontend ids (both in `src/domains/registry.test.ts`). The new text —
"the backend list holds ONE id and the frontend list two, so the accident turns on
three names across two suites" — is exact. The false clause is gone.

The strengthened argument is sound, and it is a better argument than the one it
replaced:

- The claim that `unexpected` and `repaired` are **structurally empty** against a
  dropped-file run is correct, not merely probable: both are computed from the
  report's own failures, and a dropped file contributes neither a failure nor a
  pass. `missing` (`allowed - ran`) is genuinely the only rule that can fire.
- That `missing` fires only when a dropped file happens to carry an allowlisted id
  is therefore an accident of which files were lost, and its strength does scale
  with the allowlist — three names is the entire surface today.
- The self-pruning direction is the right thing to lean on. This repo's own
  allowlist file states that an entry whose test starts passing **fails the job**
  with an instruction to delete the line. So the list only shrinks except when
  someone deliberately adds to it, and the accidental catch is strongest today and
  weaker every time the list does its job. A guard whose only backstop gets weaker
  as the codebase gets healthier is not a backstop.

That reasoning does justify the floor independently of the allowlist's contents,
which is what the clause needed to do. The "none of this is a defect in the
allowlist" framing carried into the JSON is also correct and worth having there:
the allowlist answers "did anything new break", and nothing was asking "did the
suite run".

---

## Nothing regressed

| check | result |
|---|---|
| `python scripts/ci/test_assert_known_failures.py` | **43 controls, all green**, `all negative controls passed` |
| real backend report (441 files / 5251 cases), default `--floor` | `suite size held` → **exit 0** |
| real frontend report (860 cases, legitimately red on 2 allowlisted) | `suite size held` → **exit 0** |
| backend trim (88 of 441 files dropped) | `THE SUITE SHRANK` → **exit 2** |
| frontend trim (12 of 61 files dropped) | `THE SUITE SHRANK` → **exit 2** |
| `checks.yml` | parses |

The frontend row is the one that mattered: the suite carries two real failures and
the size check still returns 0, so the floor is still not gated by the condition it
exists to doubt.

Integrity, re-checked this round rather than carried forward:

- Round-2 diff touches **three files**: the ledger, `suite_size_floor.json`,
  `test_assert_known_failures.py`. **No production source.** `assert_known_failures.py`
  and `checks.yml` are byte-unchanged since `579bef8e`.
- `scripts/ci/known_test_failures.json` is **byte-identical to trunk** — blob
  `cb4d565ef4824d4eacc2edd380e296c711d60670` on both.
- No skips, xfails, weakened assertions or deleted tests; the control count went up,
  not down.

I could not re-run `ruff` (not installed on this box); the ledger records
`All checks passed!` and nothing in the diff is plausibly a lint change.

---

## The judgement I was asked to record: does the mutation standard generalise?

**The discipline generalises; the literal four mutations do not, and the ledger's
last sentence reads the wrong way round.**

The four are, stripped of this guard's specifics: reorder two adjacent return
blocks; delete a clause from a compound validation condition; delete a
type-narrowing clause; slacken a relational comparison. Those are ordinary mutation
operators — statement reordering, condition-clause deletion, relational/boundary
mutation — and they apply to any guard, not just this one. As a *class* the standard
is sound, and the paragraph's real claim is the right one: "the gap was found by
mutating the implementation and asking which control noticed, not by reading the list
and finding it long."

But `baseline <= 0` does not exist in the next guard, and "the four mutations above
are the standard, not a one-off for this review" literally promises four specific
edits. Read literally it is the over-claim — a checklist of four textual mutations
would be cargo-cult, and the next guard would pass it while leaving its own
load-bearing line untested, which is precisely the failure this round found. The
preceding sentence ("every guard added here from now on gets the second treatment")
makes the intended reading clear, so I am recording this as a wording risk rather
than a finding.

What I would actually carry forward from this branch is narrower and stronger than
four mutations, and both halves were earned here rather than asserted:

1. **Mutate, do not count.** A control count measures what was written; a surviving
   mutation measures what is not load-bearing. 35 green controls and an untested
   precedence coexisted comfortably.
2. **Where two branches can return the same code, assert the reason.** Proven both
   ways this round: the code-only assertions survived two mutations, and a genuine
   behavioural mutation reddened eleven of them.

## Nits, non-blocking, recorded for accuracy only

- The ledger's final-state block says `all negative controls passed (41 controls)`.
  The script emits **43**. The count is under-claimed, not over-claimed, so nothing
  turns on it — but this branch's whole argument is that recorded numbers should be
  checkable, and this one is off by two.
- **I withdraw the round-1 minor note** on `SHRINK_ALLOWANCE` / 0.5%. Re-read against
  `check-bundle.js`, the comparator says "the **growth** allowance in the bundle
  ratchet is 0.5%", and `GROWTH_ALLOWANCE = 0.005` is exactly that. The note was
  over-strict; only the word "exactly" is loose, and it plainly refers to borrowing
  the two-way-ratchet mechanism. Nothing to fix.

---

Merge permitted.
