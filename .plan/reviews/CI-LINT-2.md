# RV review — CI-LINT round 2

**Branch** `feat/ci-backend-lint` **head** `91b64856039ccec423b4aa4f94f449514511a3fb`
(round 1 head `a9165b76`) **Trunk** `refactor/unified-return-platform` @ `63744f2a`
**Verdict: PASS** — F-1 resolved, nothing else moved.

---

## Scope of the round

`git diff --name-only a9165b76 91b64856` → **one file**,
`.plan/tracks/CILINT.ledger.md`, in one commit (`91b64856`, "the branch count was five
and it was six"), +68/-7. No source, no workflow, no config, no test. Round 1 verified
the substance to the digit against `a9165b76`; trunk has not moved since (`63744f2a`
then and now), so those verifications stand unchanged and I did not re-derive them.
`git merge-tree --write-tree` trunk × `91b64856` → **0 conflicts**, still.

---

## F-1 — **RESOLVED**

The requirement was that the ledger say six, name the intersecting branch, and state
plainly that the count was wrong. It does all three, and it leads with the correction
rather than burying it:

> **Corrected after RV finding F-1. … The count was wrong: there were six, and one of
> them does touch files this branch reformatted.**

`feat/live-harness-registration` is named, both files are named, and the summary line is
*"five of the six touch zero affected files; the sixth touches two"* — the correction,
not a caveat appended to the old number. `git branch --no-merged 73bd79aa` today returns
exactly the six named (the listing also carries trunk, the branch itself, the declared
`rv-calibration` fixture, and my own `feat/acc-audit-b` review branch).

### Spot-checks of the author's independent re-verification

The author re-derived my three mitigations rather than adopting them. I checked all
three plus both false positives, **in place**, not on extracted copies:

| Ledger claim | Mine |
| --- | --- |
| `merge-tree` exits 0, tree `c05a8b1a` | **exit 0, tree `c05a8b1ad2844dd97a5b1de9da742309631d1e5a`** — sha-exact at `a9165b76` |
| only unformatted region in either file is the `draft_support_request` hunk | **confirmed** — `ruff format --diff` in the live-harness worktree: exactly 1 hunk per file, and both are byte-identical to what `git diff 73bd79aa..91b64856` changed there (offsets differ only) |
| merged tree → "2 files already formatted" | **confirmed** — merged blobs written in place into an in-repo worktree: `2 files already formatted`, and `ruff check` → `All checks passed!` |
| 34-second race, 11:47:17 vs 11:47:51 | **confirmed** — `9b665a98` at `2026-08-31T11:47:17`, `00471116` at `11:47:51`; `merge-base --is-ancestor 7a898cf9 73bd79aa` → true |

Also confirmed in place: `feat/live-harness-registration`'s own copies of both files pass
`ruff check` and carry no violation other than that one hunk.

### The generalisation — sound, and honestly bounded

The stated lesson — *a branch enumeration is a perishable measurement, and this ledger
leaned on one as if it were durable* — is the right one, and the three replacement
checks are genuinely content properties: a conflict-free merge, a hunk identity, and a
gate result on a materialised tree. None of them expires with the passage of another
commit.

One precision, offered as such and **not as a finding**. The three checks are properties
of *the intersecting branch*, and knowing that it is the only intersecting branch still
comes from an enumeration — the ledger's closing line, "the other five branches intersect
zero affected files", is that same perishable instrument. So the re-grounding is not
enumeration-free in the strict sense. It passes anyway, for two reasons:

1. The dependency is **stated, not hidden**. The ledger flags enumerations as perishable
   one paragraph before it uses one, and attributes the five-branch claim to the
   enumeration rather than to the content checks. A residual dependency the reader is
   told about is not the defect F-1 named; the defect was a reader taught that an
   enumeration was complete.
2. The **decision does not rest on it**. Re-reading *"The decision — option 1"*, option 1
   is chosen on cost-of-debt and on option 2's permanent machinery — "one `--fix`, six
   one-line `from err` edits, and one `ruff format`" against a baseline file, a
   comparator, and a Gate-0 negative-control self-test. Collision evidence supports the
   "cheap now" half; it is not the pillar. My round 1 called that measurement
   "load-bearing evidence for choosing option 1 over option 2" — on re-reading the
   decision section I overstated its weight, and I record that here rather than let it
   stand.

The durable form of the argument is present in the ledger's own words either way: the
94 files are raw `ruff format` output, and new files are already format-clean — so any
future intersecting branch resolves the same way this one does, by re-running the tool.

### The two false positives — accurately recorded, and reproduced

Both matter to me as much as to the author, so I reproduced them rather than reading them.

- **Line-length 88 vs 100.** `backend/pyproject.toml:91` sets `line-length = 100`. On a
  copy of `test_return_case_workflow_real_infra.py` in a scratch directory,
  `ruff format --diff` reports **29** hunks; with `--config` pointed at the real file it
  reports **0**; in place in the worktree it reports **1** (the known hunk). "Dozens of
  phantom wrap hunks" is accurate, and the author was right that it nearly earned a
  finding.
- **Invented `I001`.** With `--config` supplied but the file outside the repo, `ruff
  check` reports `I001` on both merged copies **and on this branch's own copy**, which is
  clean in place — exactly the control the ledger describes. (Without `--config` the
  default selector omits `I` entirely, which is why the FP only appears on the
  configured re-run — consistent with the sequence the ledger narrates.)

**The trap turned on my own round 1.** Every round-1 count was taken in a detached
worktree of the repo with `backend/pyproject.toml` present, not on extracted copies —
and round 1 §5 measured the 88-vs-100 divergence deliberately for the root-path step, so
the config-discovery effect was already under control. The one round-1 measurement whose
provenance was worth re-taking is the F-1 mitigation `ruff format --diff` on the
live-harness files; I have re-taken it in place above, and it holds.

---

## Nothing else moved — CONFIRMED

- `scripts/ci/known_test_failures.json` blob `cb4d565ef4824d4eacc2edd380e296c711d60670`
  at base `73bd79aa`, at `a9165b76`, at `91b64856`, and at trunk `63744f2a`. Identical to
  all four. Not widened, not touched.
- No production code, workflow, or config changed since round 1 — the diff is one
  markdown file.
- No new frozen-module import: `git diff a9165b76 91b64856 | grep -E
  "^\+.*(associate_flow|order_discovery|associate_returns|return_agents)"` → empty.
- Base `73bd79aa` still a genuine ancestor of trunk; merge still conflict-free.

The ledger's own *"How this was reviewed"* list now carries the round-1 verdict, the
finding, and what changed in response. That is the right place for it.

---

## Verdict

**PASS.** Zero unresolved findings. The correction says six, names the branch, names the
two files, and states the original count was wrong without hedging it into a caveat; the
three replacement checks reproduce independently, one of them to the tree sha; and both
recorded false positives reproduce exactly as described. Round 1's substance is
undisturbed. Merge permitted.

The round-1 *"reported, not findings"* items stand as reported and are not gates on this
branch: the `.env` precondition that makes the pre-existing `backend` job unrunnable on a
fresh runner, and the stale CI section of `.plan/merge.md`. Both want their own dispatch.
