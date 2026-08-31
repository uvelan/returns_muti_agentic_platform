# GUARD ledger — the fabrication guard learns the ternary

Append-only. One entry per step, per contracts.md §3.

Branch `feat/fabrication-guard-ternary`, base
`921041c51cb9909dacb2c9c42f6a0d7553ea9d10` — the head of
`refactor/unified-return-platform`, read from the repo rather than taken from
the dispatch.

Scope: `frontend/src/domains/returns/ReturnCopilotFabrication.test.ts` and this
ledger. **Nothing else.** A fabrication the extended guard newly catches is a
dispatch to the owning slice, never a commit here.

---

## step:00 — base verification

**Repo state on arrival.** The worktree was checked out on
`worktree-agent-a7a541870b02e5def` at `0448d32`, an old `master` commit where
`.plan/` does not exist. The dispatch's own environment snapshot named
`24e01b1` as the head of the trunk branch; `git rev-parse
refactor/unified-return-platform` says **`921041c5`**, and `24e01b1` is
reachable from nothing in this repo. The branch was cut from the read head, not
the quoted one. Not a halt — only the worktree pointer and the snapshot were
stale.

`frontend/node_modules` was absent; `npm ci` (exit 0).

**Anchors verified present before any writing:**

| Anchor | Path | Verdict |
|---|---|---|
| The guard | `frontend/src/domains/returns/ReturnCopilotFabrication.test.ts` | present; `AUDITED`, `SHAPES`, `code()`, `sources()`, `productionSources()`, and the `??` regex test all as described |
| The hole | that file's last `it`, `/\?\?\s*"(?!Pending"…)/` | present — a line regex, `??` only |
| A parser | `typescript@~5.6.2` in `frontend/package.json` devDependencies | present, so an AST walk is available rather than a second regex |
| The AST precedent | `backend/tests/test_fact_name_literals_live_only_in_fact_names.py` | present; read for the shape of the argument (prose is not a literal; a scanner that finds nothing is indistinguishable from one that looks for nothing) |
| Recurring failure shapes | `.plan/merge.md` §"Recurring failure shapes" | read; the newest shape (an injection red for the wrong reason) governs the evidence in step:02 |

**Baseline, before any edit:** `npx vitest run
src/domains/returns/ReturnCopilotFabrication.test.ts` → **35 passed**. This
reproduces the reviewer's number exactly, which is what says the file on disk
is the file the hole was proved against.

---

## step:01 — the fallback scan becomes a parse

**Files touched:** `frontend/src/domains/returns/ReturnCopilotFabrication.test.ts`.

The `??` line regex is **replaced**, not supplemented. A second regex would
have closed the one idiom the reviewer happened to write and left the next one
open; the position both idioms share is only visible in a tree. Each file is
handed to `ts.createSourceFile` (TSX for `.tsx`, TS for `.ts`) and walked for
*a constant standing where a value goes when the value is missing*.

Raw text is parsed rather than `code()`-stripped output: a comment produces no
node, so the commentary immunity `code()` was built for comes for free, and
reported line numbers now match the editor's.

**Covered:** `??`, `||`, a ternary on any explicit absence test (`=== null`,
`== null`, `!== null`, `=== undefined`, `typeof x === "undefined"`, `x === ""`,
`x.length === 0`, `!x`) in either polarity, `if (absent) { y = "…" }` with or
without an `else`, `if (absent) { return "…" }` where the other path returns
something computed, default parameters, destructuring defaults, and a sentence
split by `+` (folded before it is judged).

**Known-uncovered, written into the file's docstring** rather than left for the
next reader to assume: `useState`/`useRef` seeds, ternaries on bare truthiness,
JSX elements in the absent branch, templates with substitutions, values
imported from outside the domain or assembled from a variable, and compound
conditions.

**Two decisions that kept the guard quiet rather than loud**, each closing a
false positive structurally instead of by allowlist:

1. **A pair of constant branches is a wording choice, not a fallback** — no
   value renders either way. This is why `printable === null ? "No label…" :
   "Authorized RMA manifest…"` is legal. Folding `+` is what lets
   `ReturnCopilotPage.tsx:468` (a two-line `new Error(…)` message) be seen as
   the pair it is.
2. **A guarded `return` is only a fallback if the other path returns the
   value** (`statementAfter`). Without this the four-refusal validation ladder
   in `ClarificationsSection.check` reads as four fabrications, and that is
   precisely the noise someone suppresses.

**The vocabulary of absence words is now a named set** shared by every idiom,
with a membership test stated in the file: an entry must *report* an absence,
never fill it. Seven merged strings were surfaced and read against that test
before being admitted — enumerated in the delta report. `"Delivered on time"`
cannot join, because it reports a delivery.

**Anti-tautology:** `fallbackPositions` deliberately does *not* apply the
vocabulary, and a test asserts the walk still sees `ItemSelectionMode.tsx`'s
real fallback positions. A walk that silently stopped parsing would otherwise
report the same clean tree a genuinely clean tree reports.

**Commands and results.**

| Command | Result |
|---|---|
| `npx vitest run src/domains/returns/ReturnCopilotFabrication.test.ts` | **60 passed** (35 → 60; +13 covered-idiom probes, +9 forgiven-shape probes, +1 anti-tautology, −1 replaced regex test, 1 rewritten real-source scan) |
| `npx eslint src/domains/returns/ReturnCopilotFabrication.test.ts --max-warnings=0` | clean |
| `npx tsc -b --pretty false` | 3 errors, **all pre-existing** — verified by stashing the change and re-running (3 before, 3 after); all three are the `actorId` optionality mismatch in `CaseOperationsPage.test.tsx`, `ReturnSetupCapture.test.tsx`, `SupportConsolePage.test.tsx` |
| `npx vitest run` (full suite) | 747 passed, **2 failed** — both the known pre-existing `registry.test.ts` `/shipments` collisions. Zero new. |

**Next step:** step:02 — the reviewer's own probe, planted in both forms, with
the injection verified to have done what it claims.

---

## step:02 — the reviewer's probe, both forms, and the tell

**Files touched:** this ledger. No source change — the injection is planted,
measured and reverted, and the tree is clean at the boundary.

**Site.** `frontend/src/domains/returns/modes/ReturnHistorySection.tsx:76`,
which renders `{record.returnReference ?? "RMA pending"}` — a real business
value (the support-issued RMA) behind a sanctioned absence word. Replacing that
word with a fabricated one is exactly the defect the audit found, at a line
that renders to an associate.

**The matrix.** Both forms of the same fabricated string, against both versions
of the guard. Each cell is a run of
`npx vitest run src/domains/returns/ReturnCopilotFabrication.test.ts`.

| | `?? "Delivered on time"` | `=== null ? "Delivered on time" : …` |
|---|---|---|
| guard at base `921041c5` | 1 failed / 34 passed | **35 passed — the hole** |
| guard at `d90bb9bd` | 1 failed / 59 passed | 1 failed / 59 passed |

The top-right cell reproduces the reviewer's finding exactly (35 passed), under
this agent's own hands rather than on report, and the bottom row is the fix:
**both forms now fail.**

**Verifying the injection did what it claims** — `.plan/merge.md`'s newest
recurring shape is an injection that goes red for the wrong reason, so the red
itself is not the evidence. Four tells, each ruling out a different wrong
reason:

1. **The diff is a substitution, not a deletion.** `git diff --stat` reported
   `1 file changed, 1 insertion(+), 1 deletion(-)` and `git diff -U0` showed
   one hunk, `@@ -76 +76 @@`, with the old line out and the new line in. The
   V1p2 failure mode — anchors matching elsewhere and silently deleting a block
   — is excluded by inspection of the hunk, not assumed.
2. **The failing test is named, and it is the right one.** In every red cell
   exactly one test failed and it was the fallback scan (`keeps no literal
   fallback on a business value`; on the baseline, its predecessor `keeps no
   `??` literal fallback…`). Not a rendering test that happened to assert
   `"RMA pending"`, not the shape scan, not a parse error taking the file out.
3. **The failure message names the site *and the idiom*.** Form A reported
   ``modes/ReturnHistorySection.tsx:76: ?? falls back to "Delivered on time"``;
   form B reported ``modes/ReturnHistorySection.tsx:76: ternary falls back to
   "Delivered on time"``. Same file, same line, *different idiom* — so the walk
   recognised the construct actually written, rather than tripping over the
   string by some other route. A shape-regex hit or a leftover `??` match could
   not produce the word `ternary`.
4. **59 of 60 still passed in both red cells.** Had the injection broken the
   parse, the walk would have seen an empty tree and gone *green*; had it
   broken the file, the anti-tautology test (`still finds this domain's real
   fallback positions`) and the glob-reach test would have gone red too.
   Exactly one red, and it is the assertion under test.

**Revert verified, not assumed.** `git checkout HEAD --` on both files, then
`git status --short` empty and line 76 grepped back to `"RMA pending"`.

**Commands and results.**

| Command | Result |
|---|---|
| `npx vitest run` (full suite, after revert) | 747 passed, 2 failed — the two known pre-existing `registry.test.ts` `/shipments` collisions. Zero new. |
| `RETURN_PLATFORM_PYTHON=…/backend/.venv/Scripts/python.exe npm run contracts:check` | passed; `git status` clean afterwards, so no regenerated drift |
| `git status --short` | clean |

**No dispatch raised.** The extended guard found no fabrication in merged
source. It did surface seven merged literals in fallback positions; every one
was read against the vocabulary's membership test — *does it report an absence
or fill it* — and every one reports. They are admitted to
`ABSENCE_VOCABULARY` with the file naming each, not silenced. Two of them,
`"This reply was empty."` (`CasePanel.tsx:198`) and `"This reply is empty.
Rebuild it before sending — Support would receive nothing."`
(`SupportReplyBody.tsx:95`), are two different sentences for the same state on
two panes; that is a UX-copy observation for the owning slice, not a
fabrication and not a change made here.
