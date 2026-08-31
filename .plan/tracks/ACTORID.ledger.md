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
