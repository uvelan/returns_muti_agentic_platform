# RV review — `actorId` fixture optionality (the falsified diagnosis), round 1

- **Branch:** `feat/actorid-required`, head `b94d4257`, off `85dc4271`
- **Diff:** `git diff 85dc4271..b94d4257` — 4 files, +313/-0: three test fixtures (5 lines each) and the ledger.
- **Charter:** `.plan/tracks/RV.brief.md`. Analysis: `.plan/tracks/ACTORID.ledger.md`.
- **Reviewer:** RV (second instance) — Date: 2026-08-31

## Verdict: PASS

Zero findings. **Additions only, and no Python anywhere in the diff — confirmed.**

Before anything else: **my diagnosis in `GUARD-1.md` was wrong, and the agent was right to
halt on it.** I have reproduced both falsifying probes under my own hands rather than
accepting the ledger, and the landed fix, its verification protocol, and the deferral are
all correct. I also tested the one ordering the agent did not try, and it holds.

---

## My error, stated plainly

In `GUARD-1.md` I wrote that `actorId` reached `CaseFactProjection` with a Python default,
that FastAPI therefore marked it non-required, and that `openapi-typescript` therefore
rendered it optional — and I recommended making the field required in Python and
explicitly **not** patching the fixtures.

Every link after the first is false, and the reason is thirty lines above the code I was
reasoning about. `frontend/src/api/cases.ts:51`:

```ts
type Served<T> = T extends readonly (infer Element)[]
  ? readonly Served<Element>[]
  : T extends object
    ? { [K in keyof T]-?: Served<Exclude<T[K], undefined>> }
    : T;
```

`-?` strips optionality and `Exclude<T[K], undefined>` strips `undefined` from the value
type, **at every level**. So `Served<CaseFactProjection>` is required-and-nullable no
matter what the document says. The schema was never in the causal path, and the fix I
prescribed could not have moved the error count. Worse, the docstring immediately above
`Served` says exactly this — *"`openapi-typescript` renders a Pydantic `X | None = None`
field as optional and nullable… So `undefined` is a value this API cannot produce"* — so
this was readable, not subtle. I reasoned from the generated type to the schema without
checking the alias sitting between them.

The real cause is the one the agent found: `actorId` postdates the three helpers and is
the only field not written longhand, arriving through a `Partial<>` spread, which
TypeScript types optional. That also dissolves the coordinator's argument against patching
the fixtures — which was downstream of my diagnosis. The fixtures' optionality never flowed
from the schema, so writing the field longhand concedes nothing about the REST view.

**Both falsifications, reproduced by me:**

| Probe | Setup | Result |
| --- | --- | --- |
| **A** | `.d.ts` hand-patched `actorId?: string \| null` → `actorId: string \| null` (exactly what a correct regeneration emits), three fixture lines removed | **3 errors**, same three files, same coordinates `(80,3)`, `(156,3)`, `(131,3)` |
| **B** | `.d.ts` left optional, one fixture line restored | **2 errors**, the `CaseOperationsPage` file cleared |

Probe A is the decisive one: with the document already saying what I asked it to say, the
errors are untouched. The halt was correct, and halting *before* paying for regeneration
was the right sequencing.

## The landed fix

Three helpers, one line each, `actorId: null` written **longhand inside the object
literal** rather than left to `overrides`, with a comment at each site giving both halves
of the reason: the other fields are longhand (which is the only reason they were not
failing too), and `null` is honest because these helpers build **observations**, which
have no actor. That matches the semantics S1 phase 1b established for the field — `None`
means *not command-originated*, not *unknown*.

Nothing is suppressed, no type is widened, no `as` cast, no `@ts-expect-error`. The fix
makes the fixtures state a fact about themselves that was previously left implicit.

## The verification protocol — reproduced, and it is as strong as claimed

The agent's argument is that a bare `3 → 0` is exactly the result that can be right for the
wrong reason, because `tsc -b` is incremental and a short-circuiting checker prints the
same nothing as a passing one. So it removed each line individually. I ran the same
protocol myself:

```
remove ops line  -> exit 2, exactly 1 error: CaseOperationsPage.test.tsx(80,3)  TS2322
remove ret line  -> exit 2, exactly 1 error: ReturnSetupCapture.test.tsx(156,3) TS2322
remove sup line  -> exit 2, exactly 1 error: SupportConsolePage.test.tsx(131,3) TS2322
all restored     -> exit 0, 0 errors
```

Reproduced exactly. **The three claims it makes each hold:**

1. **The checker is live, not cached.** A short-circuited run cannot emit a fresh error at
   fresh coordinates. Three did.
2. **Each line is individually load-bearing** — one error per removal, never two, never
   zero. "Never two" is the half that matters: it rules out one removal masking or
   displacing another.
3. **The green comes from those errors being fixed**, not from a file dropping out of the
   check set — because a file that had silently stopped being checked would yield *zero*
   errors on its removal, not one. Each removal positively demonstrates its file is still
   being analysed.

**One external cross-check the agent could not claim for itself:** the three coordinates
match the baseline **I independently recorded in `GUARD-1.md`** — `(80,3)`, `(156,3)`,
`(131,3)` — from a different branch, before this one existed, when I was confirming those
errors were pre-existing. The errors being fixed are demonstrably the errors originally
reported, on evidence written down before anyone knew what the fix would be.

### The ordering it did not try — I tested it, and it holds

The residual worry with `tsc -b` is real and specific: `tsc` writes `.tsbuildinfo` on
success, and a failing run may leave it untouched. So the sequence *(green) → remove →
(red, buildinfo not updated) → restore* could in principle leave the inputs matching the
last **successful** buildinfo, letting the next run skip the project and print nothing —
a green that is genuinely stale. That is exactly the remove/restore cycle the protocol
uses, and it is the ordering the agent did not probe.

I probed it. After a full remove → red → restore cycle, I injected an **unrelated** type
error into the same file and re-ran:

```
src/domains/operations/CaseOperationsPage.test.tsx(540,7): error TS2322
src/domains/operations/CaseOperationsPage.test.tsx(540,7): error TS6133   exit=2
```

Caught immediately, at its own coordinates. `tsc -b` genuinely re-checks after an error
cycle; the buildinfo is not left in a state that short-circuits the following run. The
concern is sound in principle and **does not materialise here**, which is the answer the
protocol needed and did not have. Worth adding to the ledger, since the next person to
lean on `-b` will have the same worry.

## The deferral — your ruling is right, and so is the instrument

**Deferring the Python required-and-nullable change is correct.** It is a document-honesty
fix, not a defect fix: nothing in the tree is wrong today, the typecheck is provably
invariant to it, and its cost is five `CaseFactProjection(...)` sites plus six regenerated
artifacts. Landing it inside a three-fixture branch would have coupled an unrelated
regeneration to a five-line change, and — given how this dispatch started — a regeneration
justified by a theory nobody had tested yet.

**The unrequested warning is the most valuable thing in the ledger.** Telling the next
agent *not* to verify that change with `tsc` is exactly right, and it is the failure shape
this run keeps producing: probe A proves the typecheck cannot move, so any red seen during
such an injection would be red for an unrelated reason and would be read as confirmation.
An agent adding a caution against a false positive it will not itself encounter is doing
the thing that makes a hand-off worth having.

**The named instrument is the right one**, with one refinement. Asserting `"actorId"` in
the schema's `required` array and `actorId: string | null` without the `?` in the
generated `.d.ts` inspects the artefact whose honesty is actually in question, which
`tsc` cannot. The refinement: that second assertion must target the **generated file
itself**, not any `Served<…>`-derived alias — `Served` strips the `?` regardless, so an
assertion written against the consumer type would pass vacuously and prove nothing. That
is the same trap in a new costume, and it is worth writing into the follow-up.

**`CaseFactView` correctly stays out of scope, and the cited reason is the right one.** I
read `test_case_fact_actor.py:101` — `test_no_stored_key_is_required_that_was_not_required_before`
— and its docstring states a **stored-document** constraint: *"Every field the model does
not default is a key an existing document must already carry."* Making `CaseFactView.actorId`
required would break every pre-actor fact in the log. `CaseFactProjection` is a response
DTO with no stored documents, so the constraint does not transfer. The split is principled,
not convenient.

**One observation that changes the shape of the deferred work.** `CaseFactProjection`'s
`required` array in the committed document today is:

```
required: ['factId', 'factName']
```

`actorId` is absent — but so are nine other fields the response model always serialises.
The dishonesty the deferral describes is **not `actorId`-specific**; it is a property of
the whole projection, and it is precisely why `Served<T>` exists. So the follow-up should
either make the whole projection honest or state explicitly that `actorId` is being singled
out and why. Fixing one field of eleven would leave the document exactly as misleading to a
generated third-party client, at the same regeneration cost. Not a finding — the deferral
is post-gate — but it should reach the follow-up before someone scopes it to one line.

## Verified and not contested

- **No Python in the diff**, and no backend file of any kind. The backend was correctly not
  run: there is nothing in this change a backend suite could observe.
- **`npm test`: 747 passed / 2 failed**, the two being the known `registry.test.ts`
  `/shipments` pair I confirmed pre-existing during the V1 phase 1 round. Zero new.
- **`tsc -b`: exit 0, 0 errors** at the head — and, per the protocol above, that green is
  demonstrably earned rather than cached.
- **Test integrity (rule 10):** nothing deleted, skipped, widened or cast away. Three
  fixtures gained a field they should always have stated.
- **Scope:** the branch touches only the three files the errors named, plus its ledger.
- I confirmed the trunk V2-frontend failure the coordinator mentions is not observable
  here: this branch is off `85dc4271` and its suite shows only the known pair.
- Both probe worktrees reverted and verified clean after every injection.

## Advisories

- **A1 — record the `-b` staleness probe in the ledger.** The protocol's reasoning is
  right but incomplete: it establishes that each *removal* is detected without
  establishing that the *final green* is not itself stale after a remove/restore cycle. I
  showed it is not, by injecting an unrelated error immediately after a restore. Writing
  that down means the next person does not have to re-derive the worry or, worse, skip it.
- **A2 — the document-honesty follow-up is wider than one field.** See above:
  `required` is `['factId', 'factName']`. Scope it deliberately.
- **A3 — for my own successors, and for me.** The lesson of this branch is not that a
  diagnosis was wrong; it is that a *reviewer's* diagnosis was carried into a dispatch as
  an instruction, and only halted because the agent tested the instruction before paying
  for it. When I name a cause, I should say what I verified and what I inferred. In
  `GUARD-1.md` I inferred the whole chain from a generated type without reading the alias
  between it and the schema, and I stated it as a finding-grade cause. A dispatch built on
  it would have regenerated six artifacts and fixed nothing.
