# ACC3 round 3 — `feat/acc-audit-b`

**Verdict: PASS** — zero unresolved findings. F2 is closed, the sweep that
followed it is correct, and the one judgement call in it was the right call.

Head under review `41afe878fc5495172b5352b88160faa72b5ff086` (round 2 head
`e00a532c`). Rounds 1 and 2 are carried forward and not re-derived.

## The diff is documentation-only — confirmed

`git diff e00a532c 41afe878 -- backend/ scripts/` is **empty**. Not
`backend/src/` — the whole subtree, tests included. The suite this head carries
is byte-identical to the one rounds 1 and 2 measured, so every measurement in
those rounds is valid here without re-running.

```
.plan/acceptance/STATUS.md            10 +-
.plan/acceptance/category-b-audit.md   2 +-
.plan/merge.md                        21 ++-
.plan/reviews/ACC3-2.md              195 +++
.plan/reviews/CI-ENV-{2,3,4}.md      306 +++
.plan/tracks/ACC3.ledger.md           73 ++
```

The `review(...)` and `docs(merge)` commits riding along are mine and the
orchestrator's; the single `(ACC3)` commit is the F2 fix.

Also still true against trunk: `git diff refactor/unified-return-platform...41afe878
-- backend/src/ scripts/` empty; `scripts/ci/known_test_failures.json`
byte-identical to trunk; `git merge-tree --write-tree` resolves to a single tree
with no conflict section; `git status --porcelain` empty at the end of this
review.

---

## 1. F2 — closed, and the reference now resolves

`STATUS.md:169` now states the claim directly: no branch of any patch gate
exercised in `test_cumulative_support_outcomes.py`, **both limbs of all three**,
with all three patch ids named inline, and the mechanism (`_Runtime` has no
`patched` at all). That is the measured scope, in the row an acceptance auditor
scans first. The superseded clause is gone from it.

**The reference defect was worse than I diagnosed, and ACC3 is right about
why.** I checked the structure rather than the report. STATUS.md carries two
numbered lists, both starting at 1:

| section | line | items |
| --- | --- | --- |
| `## Production defects` | 237 | 1–2 |
| `## Findings handed to their owners (reported, not repaired)` | 252 | 1–5 |

So the old "production finding 3" named the wrong list *and* overshot the only
list that has a 3 — there is no production finding 3; `Production defects` stops
at 2. My round-2 diagnosis ("finding 3 in that file is `pin_routing_decision`'s
early return") was reading the second list while saying the first. The item I
was pointing at is `Findings handed to their owners` **3**. Sustained as a
defect, corrected in its description.

The new reference — `See "Findings handed to their owners" **4**` — names the
list and resolves to line 273, the ACC3 merge-tip/patch-gate finding. Right
list, right item. The finding's own body was also repaired: "finding 1's class"
→ "**this list's** finding 1", plus a parenthetical stating that both lists in
the document run from 1 and that references here always mean the second. That
parenthetical is the durable fix — it closes the ambiguity for the next writer,
not just this reference.

No bare "production finding N" cross-reference survives anywhere in
`.plan/acceptance/`.

## 2. The judgement call on `STATUS.md:76` — endorsed

**The restraint was right, and the row is still true as written.**

I checked the row's source rather than the annotation. ACC-2's evidence
(`items-14-17-review-across-a-kill.md:150-162`) flips
`workflow.patched(_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE)` **both ways**: forced
`True` → 1 failed (`test_a_legacy_history_opens_support_instead_of_wedging`);
forced `False` → 19 failed across the gate suite. Item 20 is the deploy-replay
pair, one gate, two branches — and both branches redden. "Both patch branches
audited by flipping the decision each way" is exactly and only what that
measurement supports, and it is true.

ACC3's finding is about a *different* module's coverage of *all three* gates.
It does not touch item 20's scenario, so weakening the row would have made the
record less accurate, not more. Doing that to look thorough would have been the
same defect this audit exists to name — a row that misdirects its reader — just
pointed the other way. Weakening a verified claim you did not re-audit is also
an ownership breach in miniature: overwriting another slice's measurement with
your own uncertainty.

Does the annotation give a reader what they need? Yes, on all three counts it
has to carry: it says the row is ACC-2's own scenario and stands; it says the
row is silent about patch-gate coverage elsewhere; and it points at the finding
that records where none exists. The explicit "ACC3 did not re-audit this row"
is the part that makes it honest — it marks the boundary of who verified what,
which is the same discipline as the map's "absence here means unverified".

The cell is long for a table, and the annotation would read better as a footnote
than inline. Style, not a finding; not blocking.

## 3. The four surviving "one branch" hits — all quotes, none live

Checked each in context, not on characterisation:

| site | context | live claim? |
| --- | --- | --- |
| `STATUS.md:290` | *"an earlier draft said 'one branch of one pair', which would have sent the owner to do too little (RV ACC3-1 F1)"* | no — quoted and immediately repudiated, in the sentence that states the correct scope |
| `category-b-audit.md:176` | *"an earlier draft of this document said … which was too narrow — corrected after RV finding ACC3-1 F1"* | no — quoted, labelled superseded, cites the finding |
| `category-b-audit.md:197` | *"both limbs of all three. **Not one branch of one pair.**"* | no — the negation of the old claim |
| `category-b-audit.md:202` | *"the understated version would send them to fix one branch and believe they were done"* | no — the counterfactual explaining why the correction matters |

Every one is inside prose that asserts the corrected scope in the same or
adjacent sentence. None asserts the superseded scope. Keeping them is right:
a correction a reader cannot see the shape of is harder to trust than one that
shows what it replaced. (`merge.md:85-87` and the ledger are the same pattern.)

## 4. `category-b-audit.md:227` — reference not broken

I judged this row clean in round 2 and it was: "see production finding 1"
resolved to `## Production findings, reported not repaired` §1 (line 158), the
merge-tip finding, which is the correct target. So the risk here was a fix
breaking something that worked.

It did not. The new text names the section — `see **"Production findings,
reported not repaired" 1**` — which is the same §1 at line 158. Same target,
now unambiguous. ACC3 is right that this document has the identical collision:
`## The two genuine coverage holes` (line 43) also numbers from 1, so a bare
"finding 1" could resolve two ways here too. The added parenthetical says so
explicitly. The row also gained the corrected scope, which it previously lacked.

Closing the class in one document and not the other would have been the exact
miss this round was about. Grepping for the class rather than the instance was
the correct response to F2, and it is the response I'd rather see.

## 5. Nothing else moved

- No source change — against round 2 or against trunk.
- No test change: `git diff e00a532c 41afe878 -- backend/tests/` empty.
- `scripts/ci/known_test_failures.json` byte-identical to trunk.
- No `skip`/`xfail` added; no test deleted. Unchanged from round 2 by
  construction — the subtree is byte-identical.
- Suite unchanged: `1 failed, 5240 passed, 10 skipped, 514 deselected`, the one
  pre-existing registered failure.

---

## Round summary

One documentation commit, and it did three things right: it stated the corrected
claim in the row that carries it, it fixed the reference by naming the list
rather than trusting a number, and it swept the class instead of the instance.
The judgement to annotate `STATUS.md:76` rather than weaken it is the one I want
on the record as endorsed — the row is true, ACC3 did not re-audit it, and
saying both is better than hedging a measurement it does not own.

Merge permitted.
