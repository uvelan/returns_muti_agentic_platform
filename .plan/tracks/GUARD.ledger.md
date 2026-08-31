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
