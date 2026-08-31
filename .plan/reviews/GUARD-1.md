# RV review — the fabrication guard's ternary hole, round 1

- **Branch:** `feat/fabrication-guard-ternary`, head `42f4b4be`, cut from `921041c5`
- **Diff:** `git diff 921041c5..42f4b4be` — 2 files, +666/-14: the guard and its ledger.
- **Origin:** RV review `V3f-1.md`, where I reproduced the hole — the same fabricated string caught as `?? "Delivered on time"` (1 failed) and invisible as `=== null ? "Delivered on time" :` (35 passed). V3's clarifications card had four ternaries.
- **Charter:** `.plan/tracks/RV.brief.md`; failure shapes `.plan/merge.md`.
- **Reviewer:** RV (second instance) — Date: 2026-08-31

## Verdict: PASS

Zero findings. **Test-only, confirmed**: no file outside the guard and its ledger is
touched.

The replacement is genuinely broader rather than differently-shaped — I measured it at
**15 idioms to the regex's 1**. The 2×2 evidence reproduces exactly under my own hands,
including my own number at base. The uncovered list is honest: all six omissions produce
zero findings when probed. And the one tell I was asked to press on is **weaker as
stated than the mechanism that actually backs it** — there is an anti-vacuity test that
covers the concern properly, and I proved it fires.

Two items for dispatch, neither this branch's: a pre-existing `tsc -b` break, and the
UX-copy inconsistency the guard surfaced.

---

## Replacing the regex rather than supplementing it — the right call, and measurably so

You asked whether the walk is genuinely broader or merely different. I injected every
idiom the docstring claims, all at once, into a real component:

```
2  "?? falls back"                (x ?? "…"  and  x ?? "Delivered " + "on time")
8  "ternary falls back"           (=== null, !== null, == null, === undefined,
                                   typeof === "undefined", === "", .length === 0, !x)
1  "default parameter falls back"
1  "destructuring default falls back"
+3 (||, if-assignment, if-return)
── 15 findings for 15 idioms
```

The regex it replaces recognised exactly **one** of those fifteen. So the slice's argument
— *"adding a second regex would have closed the one idiom the reviewer happened to write
and left the next one open"* — is not rhetoric; it is the measured difference between 1
and 15. Replacing was right.

**Dropping `code()` lost nothing.** `code()` is retained and still used by
`productionSources()` for the named-literal and shaped-literal scans; only the fallback
scan parses raw text, and the file says why at the site. Parsing raw text is strictly
better there: a comment produces no node, so comment-immunity is structural rather than a
stripping pass that has to be kept correct, and the line numbers in a failure now point at
the real line. I verified the reported line (`:76`) against the file.

## The uncovered list is honest, and I probed it rather than reading it

All six claimed-uncovered idioms — a `useState`-shaped seed, a bare-truthiness ternary,
JSX in the absent branch, a template with substitutions, `"Delivered " + variable`, and a
compound condition — produce **zero** findings: `60 passed`. No accidental extra coverage
is being sold as design, and nothing on the list is quietly covered.

**Is any omission load-bearing?** Three are clearly sound: a seed is overwritten before
render or reaches the screen through a covered position, and admitting `"idle"` /
`"conversation"` would corrupt a vocabulary of *absence words* to buy a weak vector; a
bare-truthiness ternary is syntactically identical to `isOpen ? "Hide" : "More"`, so
covering it buys false positives; a variable-built or imported value is out of one file's
reach by construction.

**JSX in the absent branch is the one worth naming**, because it is the same class as the
hole just closed: a plausible sentence rendered to an associate where a value should have
been. I checked how much of it actually escapes rather than asserting the worst — a
fabricated **RMA** in a JSX absent branch is still caught, by the shape scan (2 failed).
So the residual is narrower than it first looks: it is a *shapeless plausible sentence*
inside JSX. Real, enumerated honestly, and the highest-value next increment — see A1.

## The evidence and its four tells — the 2×2 verified under my own hands

Site `modes/ReturnHistorySection.tsx:76` (`{record.returnReference ?? "RMA pending"}`),
substituting a fabrication in each idiom, run against each guard:

| | `?? "Delivered on time"` | `=== null ? "Delivered on time" :` |
| --- | --- | --- |
| **base `921041c5`** | 1 failed / 34 passed | **35 passed — invisible** |
| **fix `42f4b4be`** | 1 failed / 59 passed | **1 failed / 59 passed** |

The base row reproduces my V3f number exactly, under the slice's own chosen site.

**Tells 1–3, all confirmed on my own injection:**

1. `git diff -U0` → one hunk `@@ -76 +76 @@`, `numstat` `1 1`. A substitution, not the
   silent block-deletion that fooled V1 phase 2.
2. Exactly one test failed, and it was `keeps no literal fallback on a business value` —
   the fallback scan — not a rendering test asserting `"RMA pending"`.
3. The message: `modes/ReturnHistorySection.tsx:76: ternary falls back to "Delivered on
   time"`. It names the site **and the idiom**, and "ternary falls back to" is a sentence
   no shape-regex hit could produce.

**Tell 4, which you asked me to press on: the stated reasoning is imprecise, and the real
guard is better than the tell.**

The tell says *"a broken parse would make the walk see an empty tree and go green, not
red."* `ts.createSourceFile` is error-tolerant — it does not throw and it does not produce
an empty tree; it recovers. I tested the partially-broken case twice: with a syntax break
inserted **earlier in the same file**, and with leading garbage forcing a near-total parse
failure. **Both times the finding survived: 1 failed / 59 passed.** So the mechanism the
tell describes does not fire the way it says.

The conclusion still holds, and for a better reason the branch built deliberately:
`fallbackPositions` returns every position *sanctioned or not*, with the vocabulary applied
by the caller, precisely so a test can assert the walk still sees the domain's real
fallbacks. `still finds this domain's real fallback positions` pins `>3` positions in a
real file including both a `ternary` and a `??`. I proved it fires by making the walk
return `[]` for `modes/` — **1 failed / 59 passed**. A walk that quietly stopped parsing
turns the suite red.

So: tell 4 is sound in its conclusion, wrong in its mechanism, and unnecessary — the
anti-vacuity test does the job properly and is the thing to cite. Worth correcting in the
ledger so nobody later relies on the empty-tree reasoning.

## The two structural closures — genuine reasoning changes, not disguised allowlists

I checked the distinction that matters: does the closure name a file or a string
(allowlisting), or state a uniform structural rule (reasoning)? Both are the latter, and
**there is no per-file ignore, pragma or suppression mechanism anywhere in the guard** —
the vocabulary is the only door.

- **`ReturnCopilotPage.tsx:468`, closed by `+`-folding.** Verified by removing the fold:
  the site immediately reappears as `ReturnCopilotPage.tsx:468: ternary falls back to …`
  (2 failed). Folding is a uniform rule in `literalOf` applied to every constant `+` chain
  in every file; it closes this site because folding reveals it to be a pair of constant
  branches — a wording choice, not a fallback — and it is the same rule that *catches*
  `"Delivered " + "on time"`. One rule, cutting both ways, which is the tell of reasoning
  rather than an exemption.
- **`ClarificationsSection.check`, closed by `statementAfter`.** The rule: an early
  `return` under an absence test only stands in for a value if the path that skips it goes
  on to return the value. Without that, the shape is a validation ladder — and the file
  says so in the words that matter: *"a guard that could not tell those apart is a guard
  the next person adds a suppression to."* Calling this "the exact noise someone would
  have suppressed" is right, and closing it structurally is what stops the suppression
  habit starting.

## The vocabulary — discriminating in the way that is achievable, and no more

The stated membership test — *it must report an absence, never fill it* — is an
**editorial** criterion, not a machine check; the mechanism is an exact-match `Set`. That
is the honest reading, and it is the right design here, because what the mechanism
actually buys is that **nothing can be added silently**: there is no other escape hatch, so
every new sanctioned sentence is a reviewable line in a guard file. `"Delivered on time"`
cannot join without somebody writing it there and defending it.

The seven newly-surfaced entries are each named to their file
(`CandidateOrderMode`, `ReturnEvaluationMode`, `CasePanel`, `SupportReplyBody` ×2,
`ClarificationsSection`, `ItemSelectionMode`); the six legacy ones are generic absence
words needing no site attribution. Each of the seven reads as a report of absence:
*"source not recorded"*, *"Ordered quantity not on the source line"*, *"That could not be
recorded. Nothing was sent to Support."* None fills a gap with a plausible fact. The
dispositions are right.

**On the maintenance cost you asked about — it is the right trade for this domain, with
one condition.** The alternative to exact match is a pattern, and a pattern for "sentences
that report absence" is exactly the regex-shaped thinking that produced the hole being
fixed: it would admit the next plausible-sounding sentence by accident. Exact match makes
the guard's judgement explicit and cheap to audit. The failure mode you name is real —
people appending without thinking — but the cost of appending is deliberately high: you
must open a file whose docstring states the test, and put the sentence next to twelve
others that visibly obey it. The condition that keeps it working is that **review must
treat a vocabulary addition as a change, not as a fixture edit**; it is one line in a test
file and therefore easy to wave through in a large diff. Worth naming in the reviewer
checklist rather than trusting to attention.

---

## Two dispatches — neither this branch's

### 1. `tsc -b` is broken by three `actorId` errors — **confirmed pre-existing, and it needs a dispatch**

Verified at both commits from clean worktrees:

| | `tsc -b` errors |
| --- | --- |
| base `921041c5` | **3** — `CaseOperationsPage.test.tsx(80,3)`, `ReturnSetupCapture.test.tsx(156,3)`, `SupportConsolePage.test.tsx(131,3)` |
| head `42f4b4be` | **3** — the same three, same `TS2322` |

Genuinely pre-existing; this branch is test-only and touches none of those files.

**It is related to S1 phase 1b, and the diagnosis is specific.** The error is an optionality
mismatch on the fact projection: the generated schema types the field
`actorId?: (string | null) | undefined` while the consumer type requires
`actorId: string | null`, and three fixtures omit it. That is the signature of the
carry-forward I dispatched in `S1b-1.md` — adding `actorId` to `CaseFactProjection` —
landing with a **Python default**, which makes FastAPI mark it non-required in the OpenAPI
document, while the consuming type expects it always present.

**Recommended fix, and it should follow S1b's own discipline:** make the projection field
required in Python (no default) so the schema marks it required and every response carries
it — which is exactly the *"always written, `None` included, so no actor is
distinguishable from field absent"* rule S1b applied at the fact plane. Patching the three
fixtures instead would leave the REST view able to omit the key, which is the gap that
rule exists to close. **Dispatch to whoever landed the projection change**, not to this
slice.

### 2. Two sentences for one state — UX copy, for the owning slice

Confirmed in the source:

```
CasePanel.tsx:198        "This reply was empty."
SupportReplyBody.tsx:95  "This reply is empty. Rebuild it before sending — Support would receive nothing."
```

Same state, two panes, two sentences, and one of them tells the associate what to do while
the other does not. Correctly surfaced rather than fixed — it is not the guard's file. A
pleasing side effect of the vocabulary being an exact-match list is that the two sentences
now sit adjacent in it, which is *why* the inconsistency became visible at all.

## Verified and not contested

- **Test-only.** No production file in the diff.
- **Guard: 60 passed** (was 35). **Full suite: 747 passed / 2 failed** — the two are the
  known `registry.test.ts` `/shipments` pair, which I confirmed pre-existing at the parent
  during the V1 phase 1 round. Zero new.
- **ESLint clean** on the guard with `--max-warnings=0`. **`contracts:check` exit 0**, no
  drift (run with `RETURN_PLATFORM_PYTHON` pointed at the main tree's venv).
- **`typescript@~5.6.2` was already a devDependency** — resolved 5.6.3 in the tree, so no
  new dependency was introduced to make the parse possible.
- **Test integrity (rule 10):** nothing deleted, skipped or `.only`'d. The suite grew from
  35 to 60, and the new tests are the right two kinds — parametrised positives (*sees a
  fabricated fallback written as ${idiom}*) and parametrised negatives (*leaves ${shape}
  alone*) — so the walk is pinned in both directions rather than only against the fault
  that prompted it.
- **The stale-sha catch.** Third time on this run an agent has caught a dispatch naming a
  sha reachable from nothing and read the real head instead. Correct each time. That it
  keeps happening is a process signal about how shas are captured, not about the agents.
- All injections reverted; both probe worktrees verified clean.

## Advisories

- **A1 — JSX in the absent branch is the same class as the hole just closed**, and it is
  the highest-value next increment: a shapeless plausible sentence in
  `x === null ? <p>…</p> : x` escapes the fallback scan, though a recognisable *shape*
  inside it is still caught. Enumerated honestly in the docstring, so this is a scoping
  note rather than a complaint — but a docstring is where an omission goes to be
  forgotten. Give it a named owner or a dated follow-up.
- **A2 — correct tell 4 in the ledger.** "A broken parse would make the walk see an empty
  tree and go green" is not what `ts.createSourceFile` does; findings survived two
  deliberate syntax corruptions in my probes. The claim to make instead is the true and
  stronger one: `still finds this domain's real fallback positions` fails when the walk
  goes quiet, which I verified. Left uncorrected, a future reader may lean on the wrong
  mechanism.
- **A3 — treat a `ABSENCE_VOCABULARY` addition as a change under review**, not as a fixture
  edit. It is the guard's only door, and it is one line.
