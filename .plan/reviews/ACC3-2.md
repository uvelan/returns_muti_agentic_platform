# ACC3 round 2 — `feat/acc-audit-b`

**Verdict: CHANGES_REQUIRED** — one finding, a residual of F1, a single line.
Everything the round asked me to test came back clean, including the part that
mattered: I falsified four rows of the new map by hand and all four held.

Head under review `e00a532c2b7f92619ca9fa8218f398f2021a942d` (round 1 tip
`0fe488c8`). The branch tip has since moved to `68b7856e` on two `.plan/`-only
commits that are not ACC3's (the orchestrator's merge-doc entry and my own
CI-ENV-2 review); `git diff e00a532c HEAD -- backend/` is empty, so every
measurement below is valid for `e00a532c`. Trunk
`refactor/unified-return-platform` is now `a683f648`; `git merge-tree
--write-tree` against it resolves clean, no conflict section.

## The diff since round 1 is documentation-only — confirmed, not assumed

```
.plan/acceptance/STATUS.md            50 ++++-
.plan/acceptance/category-b-audit.md  39 +++++-
.plan/merge.md                        35 ++++-
.plan/reviews/ACC3-1.md              251 +++++
.plan/reviews/CI-ENV-1.md            253 +++++
.plan/tracks/ACC3.ledger.md           66 ++++
```

`git diff 0fe488c8 e00a532c -- backend/` is **empty** — not just `backend/src/`,
the whole subtree. The test suite of this head is byte-identical to the one round
1 measured. `git diff refactor/unified-return-platform...e00a532c -- backend/src/
scripts/` is empty. `scripts/ci/known_test_failures.json` is byte-identical to
trunk. Round 1's substance is therefore carried forward untouched and is not
re-derived here.

---

## The finding

### F2 (residual F1) — the understated sentence survives in the table an auditor reads first

`.plan/acceptance/STATUS.md:169`, the phase-3 verdict table:

> | 20 (deploy replay) | … | **remains B** — not reached. See production finding
> 3 below: one branch of the pair is unexercised in
> `test_cumulative_support_outcomes.py`. |

This is the exact clause F1 named, unchanged, in the row for the exact item F1
was about. The prose below it (production finding 4, lines 280–290) is now
correct and says the opposite — three gates, six limbs — so the document
contradicts itself, and the half a reader reaches first is the wrong half. The
category table is the artifact an acceptance auditor scans; that is the whole
argument of this audit.

Two defects in one line:

1. "one branch of the pair is unexercised" — should be the measured scope: no
   branch of any of the three patch gates is exercised in that module.
2. "production finding **3**" — finding 3 in that file is `pin_routing_decision`'s
   early return, unrelated. The correct cross-reference is finding **4**.

`category-b-audit.md:227` has the equivalent row and is clean — it says "see
production finding 1", which is correct for that file, and states no scope. Only
STATUS is affected.

**Fix:** one line. No test or source change.

---

## F1 — otherwise correctly fixed, and the supporting argument verified

Both documents now state three gates. I checked the constants and the ids rather
than the report:

| line in `return_case_workflow.py` | call | constant definition |
| --- | --- | --- |
| 1672 | `if not workflow.patched(_PATCH_V3_CLARIFICATION_ROUND_TRIP)` | 198: `= "v3-clarification-round-trip"` |
| 2247 | `if workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT)` | 157: `= "support-draft-returns-structured-payload"` |
| 2294 | `if workflow.patched(_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE)` | 174: `= "support-template-review-gate"` |

`grep -n "workflow.patched"` returns exactly these three call sites and one
comment (line 149) — the table is complete, not a sample. All three patch ids
match ACC3's table verbatim.

The supporting argument holds. `grep -c patched
tests/test_cumulative_support_outcomes.py` → **0**: the string does not occur in
the module at all, so `_Runtime` neither defines it nor has it monkeypatched in,
and any test reaching any of the three lines raises `AttributeError`. With 50 of
51 passing (round 1's measurement, unchanged tree), none of the 50 reaches any
site. The inference is sound and the enlargement is the right direction.

---

## The falsifiable map — four rows falsified by hand

I did not read this table. I deleted the mechanism and ran the test, four times,
in four different areas. Each injection was applied, run, and reverted;
`git status --porcelain` is empty at the end of this review.

**1. Budget checked before the call** (`resolution_ladder.py:439`, `>=` → `>`):

```
FAILED test_support_resolution_ladder.py::test_budget_exhaustion_writes_the_fact_and_escalates
1 failed, 35 passed
```

Fails on `assert resolver.calls == 1, "the budget is checked before the call, not
after"` with `2 == 1` — the named test, on the assertion that expresses the
guarantee, and nothing else in the file. Row holds exactly.

**2. Ambiguous asks rather than guesses** (`artifact_binding.py:155`, AMBIGUOUS →
`BOUND records[0]`):

```
FAILED test_support_message_classification.py::test_an_ambiguous_artifact_asks_rather_than_guesses
… 7 failed, 35 passed
```

The named test reddens. Six neighbours redden with it; the row claims no
exclusivity, and broad reddening on a DR-11 guess is the correct shape. Row holds.

**3. Approval checks the payload hash** (`review_aggregate.py:778`, mismatch raise
deleted):

```
FAILED test_review_aggregate.py::test_approval_refuses_a_hash_of_bytes_the_store_does_not_hold
1 failed, 50 passed
```

`DID NOT RAISE ApprovedPayloadHashMismatchError`. One test, the named one. Row
holds exactly.

**4. Disclosure on agent-authored sends** (`_with_disclosure` returns the bare
body), because this row carries a *count* — "(+11)" — and a count is falsifiable
in a way a name is not:

```
tests/operations/test_support_reply_gating.py                6 failed
tests/operations/test_support_outbound_composition.py        3 failed
tests/operations/test_support_clarification_roundtrip.py     2 failed
tests/operations/test_clarification_activities.py            1 failed
```

Twelve distinct tests, including
`test_an_auto_reply_is_delivered_with_system_provenance_and_disclosure`. The
named test plus eleven. The count is right to the test.

Four of ~18 rows, chosen across the resolver, multi-RMA binding, the review
aggregate, and outbound composition. All four are true as written. I found no row
whose named test stayed green, and none that reddened alongside tests the row
said stay green.

### The two disciplines ACC3 claims

**Names verified, not transcribed.** I grepped all 17 distinct test names in the
table against `backend/tests/`. Every one resolves to exactly one `def` in one
file — no typos, no ghosts, no name matching two places. The names are real.

**Absence is documented as unverified.** STATUS.md, immediately above the table:

> A guarantee ACC3 did not inject against is **absent from this table** —
> absence here means unverified, never "fine".

Explicit, in bold, before the first row. The map does not imply completeness, so
it does not recreate the defect it exists to fix. Paired with the preamble's own
statement of why it exists — a row naming a file asks for trust, a row naming a
deletion is checkable in a minute — this is the right instrument, and it is now
an instrument that has actually been used by someone other than its author.

---

## Nothing regressed

- `scripts/ci/known_test_failures.json` — byte-identical to trunk (empty diff).
- No `skip` or `xfail` added anywhere in
  `git diff refactor/unified-return-platform...e00a532c -- backend/tests/`.
- Exactly one deleted `def test_` line in the whole branch diff, and it is the
  renderer's `def` replaced by the `@pytest.mark.parametrize` signature directly
  above it — the same one round 1 cleared. No test deleted.
- No source change since round 1, and none against trunk.
- Full suite on this head: `1 failed, 5240 passed, 10 skipped, 514 deselected` —
  the one pre-existing registered failure. Identical to round 1, as the
  byte-identical `backend/` subtree requires.

---

## Round summary

F1's substance is fixed, correctly and with the scope enlarged in the right
direction; I verified the three constants, the three ids, and the zero-occurrence
grep that the inference rests on. The new falsifiable map is the strongest thing
in this branch, and it survived being falsified — four rows, four areas, four
holds, including one count checked to the test. Absence from it is documented as
unverified rather than fine.

One line of STATUS still says what F1 said it should not, in the row for the item
it concerns, pointing at the wrong finding number. Fix `STATUS.md:169` and
resubmit; the next round is a `git diff` and a PASS.
