# ACTORID ledger — `CaseFactProjection.actorId` required-and-nullable

Branch `feat/actorid-required`, cut from `85dc4271bba368bec41dd4273146ac94c3d02250`
(the verified head of `refactor/unified-return-platform`).

**Base sha note.** The environment snapshot handed to this agent named `24e01b1`
as the recent head; it is not. `git for-each-ref` puts
`refs/heads/refactor/unified-return-platform` at `85dc4271` and the worktree was
sitting on `master` (`0448d32a`). Branched from the ref, not from the number —
the run's standing rule.

---

## step:01 — anchors verified, defect reproduced

**Anchors (all present, none adapted).**

| Anchor | Where | State found |
|---|---|---|
| `CaseFactProjection.actorId` | `backend/src/return_platform/operations/case_projection/contract.py:186` | `actorId: Reference \| None = None` — the Python default that makes it non-required |
| `project_facts` | `backend/src/return_platform/operations/case_projection/assembly.py:472-499` | passes `actorId=_text(document.get("actorId"))` **unconditionally** — the value is always supplied |
| `append_scoped_case_fact` | `backend/src/return_platform/operations/case_repository.py:383` | writes `"actorId": actor_id` with the comment *"Always written, `None` included: a reader distinguishing 'no actor' from 'field absent' is the difference between provenance and a gap"* |
| `append_scoped_fact_once` | `backend/src/return_platform/workflows/return_case_activities.py:567` | delegates to the above |
| `CaseFactView.actorId` | `backend/src/return_platform/operations/models.py:251` | `str \| None = None` — **stays as it is**, see below |

**Why `CaseFactView` is not touched.** It is the *stored document* model.
`tests/operations/test_case_fact_actor.py:101` pins its required set against
`LEGACY_STORED_KEYS` precisely so `actorId` cannot become a key a pre-deploy
document must carry. The projection is the opposite case: it is a response DTO
with exactly one constructor, which already always supplies the value. Required
there costs nothing and is what the writer's rule already guarantees.

**Baseline `npx tsc -b` in `frontend/`: 3 errors, all TS2322 on `actorId`.**

- `src/domains/operations/CaseOperationsPage.test.tsx(80,3)`
- `src/domains/returns/ReturnSetupCapture.test.tsx(156,3)`
- `src/domains/support/SupportConsolePage.test.tsx(131,3)`

Each: `Type 'string | null | undefined' is not assignable to type 'string | null'`.

**The mechanism, end to end.** `contract.py`'s Python default → FastAPI omits
`actorId` from the schema's `required` → `openapi-typescript` renders
`actorId?: string | null` → `src/api/cases.ts:51`'s `Served<T>` mapped type
(`-?`, `Exclude<…, undefined>`) strips the optionality back off because *"the
backend never omits it"* — and the three fixtures, written before the field
existed, do not supply it.

**Why the fixtures are not the fix.** Adding `actorId: null` to three fixtures
would make the generated type's `?` correct — i.e. it would concede that the
REST view is free to omit the key. That is exactly the "no actor" vs "field
absent" collapse S1 phase 1b's writer rule exists to close, and `Served<T>`'s
own docstring is the frontend half of the same claim. Required-with-`None`-allowed
is the only shape that matches what the writer actually does.

Next: step:02 — drop the default in `contract.py`, add the owning test.

---

## step:02 — HALT. The dispatched diagnosis is falsified; the prescribed fix does not fix it.

**Nothing was landed.** `contract.py` is untouched, no artifact was regenerated,
the working tree is clean at this commit. Two probes, both reverted.

### The claim under test

The dispatch states: *the generated schema types `actorId?: (string | null) |
undefined` while the consumer requires `actorId: string | null`*, and therefore
making the Python field required will clear the three errors. The first half is
literally true of the generated file. **The inference is not**, because
`Served<T>` sits between them.

### Probe A — schema required, fixtures untouched → still 3 errors

Hand-patched `src/api/generated/return-platform.d.ts` from `actorId?: string |
null` to `actorId: string | null` — i.e. exactly what a correct regeneration
after the Python change would produce, obtained without paying for the
regeneration. `npx tsc -b`: **3 errors, the same three files, the same TS2322.**

The only thing that moved was the *rendering* of the reported source type,
`actorId?: (string | null) | undefined` → `actorId?: string | null | undefined`.
The `?` is still there, because the `?` was never coming from the schema.

### Probe B — schema left optional, one fixture supplies the field → 2 errors

Reverted the `.d.ts` to base (schema still non-required) and added a single line
`actorId: null` to `fact()` in `CaseOperationsPage.test.tsx`. `npx tsc -b`:
**2 errors.** That file's error is gone; the other two stand.

### What is actually happening

`src/api/cases.ts:51` defines

```ts
type Served<T> = T extends readonly (infer Element)[]
  ? readonly Served<Element>[]
  : T extends object
    ? { [K in keyof T]-?: Served<Exclude<T[K], undefined>> }
    : T;
```

and `CaseFactProjection` (line 103) is `Served<components["schemas"]["CaseFactProjection"]>`.
The `-?` and the `Exclude<…, undefined>` **already** normalise every field to
required-and-nullable, on the documented grounds that *"the backend never omits
it"*. So the frontend consumer type is required-and-nullable **regardless of
what the published document says** — which is why changing the document moves
nothing.

The optionality in the *source* type comes from somewhere else entirely. Each of
the three helpers has this shape:

```ts
function fact(
  name: string,
  value: …,
  overrides: Partial<CaseFactProjection> = {},
): CaseFactProjection {
  return { factId: …, factName: …, value, agentId: …, channel: …,
           acquisitionMethod: …, sourceSystem: …, observedAt: …,
           recordedAt: …, supersedesFactId: …, ...overrides };
}
```

Ten of the eleven fields are named explicitly in the literal. `actorId` is not —
it was added to the projection after these helpers were written. A property
supplied **only** by the spread of a `Partial<…>` is typed optional by
TypeScript, so `actorId` alone arrives as `actorId?: … | undefined` and fails
the required return type. Every other field is saved by being written out
longhand.

**The three fixtures are both the cause and the only lever.** The schema is not
in the causal path.

### On the "obvious fix is wrong" argument

The dispatch rejects patching the fixtures because it *"would leave the REST
view free to omit the key … the type would then be honest about a looseness we
do not want."* That argument depends on the fixtures' optionality flowing from
the schema. It does not. `Served<T>` already forbids the omission on the
consumer side, unconditionally and by construction, so adding `actorId: null` to
three test doubles concedes nothing whatsoever about the REST view — it just
makes three incomplete doubles state the honest thing (*this fact has no
actor*), which is what S1 phase 1b's rule wants a reader to be able to say.

### The Python change is still defensible — but on different grounds, and not here

Making `CaseFactProjection.actorId` required-and-nullable **is** a real
improvement: it would make the *published document* assert what
`append_scoped_case_fact` already guarantees, instead of leaving `Served<T>` to
assert it on the schema's behalf. That is a genuine contract-honesty argument
and it survives this halt intact.

It is not landed here for three reasons:

1. **It fixes none of the three errors** (probe A). Landing it under a
   `feat/actorid-required` branch dispatched to fix them would file a change
   under a result it does not produce.
2. **The dispatch's own verification protocol cannot pass.** The required
   injection — revert the Python field, regenerate, confirm the 3 errors return
   — is unpassable, because the errors never leave. Any red observed during it
   would be red for a reason unrelated to the change, which is precisely
   `merge.md`'s newest recorded failure shape.
3. **It is not free.** It costs five `CaseFactProjection(...)` construction
   sites in `tests/operations/test_case_projection.py` (lines 800, 807, 990,
   1757, 1829) and a regeneration of all six pinned artifacts — a blast radius
   that should be authorised against its actual justification, not carried in
   on a false premise.

Returned to the orchestrator for a ruling on scope. **No adaptation made.**

---

## step:02 addendum — is typecheck gated? Yes, by a script nothing runs.

The dispatch asked whether `tsc` is part of any CI/test script the repo actually
runs. The answer is sharper than "no", and worth stating precisely.

`frontend/package.json`:

```
typecheck:  tsc -b --pretty false
build:      npm run typecheck && vite build && npm run check:bundle
check:      npm run lint && npm run build
test:       vitest run
```

So `tsc` **is** a gate — `build` runs it first, and `check` runs `build`.
Measured on this branch at base: `npx tsc -b --pretty false` exits **2**, and
`npm run typecheck` exits **2**, so `npm run build` and `npm run check` are both
**red on trunk today**, and have been since S1 phase 1b merged.

What is missing is an executor. `.github/workflows/` contains exactly one file,
`secret-scan.yml` — no workflow runs `build`, `check`, `test`, or the backend
suite. And the run's own per-slice protocol asks for `npm test` (vitest, green)
and `npx tsc -b` (read, and tolerated at "3 known errors") rather than
`npm run check`, so the gate that would have caught this was never invoked.

**So it is not that nothing gates typecheck; it is that the gate is red and
nobody runs it.** A CI job running `npm run check` would have failed this merge.
Not adding one — CI configuration is explicitly out of scope for this branch.

---

## step:03 — the fixtures land; the Python change stays reverted

Both orchestrator rulings on step:02, applied.

### Ruling 1 — patch the three fixtures. **Landed.**

One `actorId: null` in each helper, written **longhand in the literal** rather
than left to `overrides`, with a comment saying why (a field supplied only by
the `Partial<>` spread is typed optional; the other ten fields are already
longhand for the same reason, by accident of having predated the field).

| File | Helper |
|---|---|
| `frontend/src/domains/operations/CaseOperationsPage.test.tsx` | `fact()` |
| `frontend/src/domains/returns/ReturnSetupCapture.test.tsx` | `caseFact()` |
| `frontend/src/domains/support/SupportConsolePage.test.tsx` | `fact()` |

`null` is the honest value: these helpers build observations, and an observation
has no actor. The doubles now *state* that instead of leaving a hole.

### Evidence: 3 → 0

`npx tsc -b --pretty false` → **exit 0, zero errors** (was exit 2, three errors).

**The tell.** A green from `tsc -b` is the one result that can be right for the
wrong reason: `-b` is incremental and reads `../.tmp/frontend-tsconfig.*.tsbuildinfo`,
so a checker that short-circuited on stale build info would also print nothing.
Observation cannot distinguish those two greens, so I did not rely on it.

I removed each of the three lines **individually** and re-ran:

```
removed actorId from CaseOperationsPage.test.tsx -> exit=2, 1 error: CaseOperationsPage.test.tsx(80,3)
removed actorId from ReturnSetupCapture.test.tsx -> exit=2, 1 error: ReturnSetupCapture.test.tsx(156,3)
removed actorId from SupportConsolePage.test.tsx -> exit=2, 1 error: SupportConsolePage.test.tsx(131,3)
all three restored                               -> exit=0, 0 errors
```

Three things this establishes that a bare `0` does not. The checker is **live**,
not serving a cached green — it produces a fresh error on demand. Each line is
**individually load-bearing** — one error per removal, never two, never zero.
And the error that returns is **the same error**: `(80,3)`, `(156,3)`, `(131,3)`
are the exact coordinates of the three baseline errors recorded in step:01, so
the green comes from those three errors being fixed rather than from some other
error having been displaced or a file having stopped being checked.

### Suite

`npm test` — **747 passed, 2 failed**, both in `src/domains/registry.test.ts`
and both the known `/shipments` pair (`returns.session.read: /returns,
/shipments, /support` against an expectation naming only `/returns, /support`).
**Zero new failures.**

**Backend not run, deliberately.** The backend is untouched by this step — the
diff is three frontend test files, additions only, no deletions. Nothing on the
Python side changed, so there is nothing there to regress.

### Ruling 2 — the Python change stays reverted, and its reasoning is preserved

`backend/src/return_platform/operations/case_projection/contract.py` is
**unmodified**; it does not appear in this branch's diff against `85dc4271`.
Recorded as a post-gate follow-up under **document honesty**:

> `CaseFactProjection.actorId` should be required-and-nullable
> (`actorId: Reference | None`, no default). The published document currently
> says the key may be absent, while `append_scoped_case_fact` guarantees it is
> always written, `None` included. A third-party client generated from that
> document would type the field optional and write defensive code for a case
> that cannot occur. That is a real contract inaccuracy, independent of any
> typecheck.
>
> `models.CaseFactView.actorId` **keeps** its default and is not part of this.
> That model validates *stored* documents, including ones written before the
> field existed, and `tests/operations/test_case_fact_actor.py:101` pins its
> required set against `LEGACY_STORED_KEYS` for exactly that reason. A stored
> document may lack the key; a served response may not. The two models get
> opposite answers because they answer different questions.
>
> **Cost:** five `CaseFactProjection(...)` construction sites in
> `tests/operations/test_case_projection.py` (lines 800, 807, 990, 1757, 1829)
> gain `actorId=None`, plus regeneration of all six pinned artifacts
> (`npm run contracts:generate` for `frontend/openapi/…json` and the `.d.ts`;
> `scripts/check_openapi_drift.py --write` for `openapi/`, `backend/openapi/`,
> root `openapi.json` and the evidence receipt), with AMENDMENT-3's
> endpoint-survival check in **all four** JSON snapshots.
>
> **Verification warning for whoever picks this up:** do **not** verify it with
> `tsc`. Per probe A in step:02 the typecheck is invariant to this change, so a
> `tsc` injection is unpassable and any red seen during one would be red for an
> unrelated reason. Verify it where it actually bites — assert `"actorId"` is in
> the published schema's `required` array for `CaseFactProjection`, and that
> the generated `.d.ts` renders `actorId: string | null` without the `?`.
>
> The no-`default_factory` contract test
> (`test_case_projection.py:481-484`) skips required fields, so it does not
> obstruct this.
