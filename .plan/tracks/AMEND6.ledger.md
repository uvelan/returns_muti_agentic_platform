# AMEND6 — executing AMENDMENT-6

Append-only. One entry per step. Every command block below is **captured**, not
transcribed: `scripts/dev/ledger_capture.sh` redirects the process's own bytes
into this file.

**Task.** `.plan/contracts.md` §1a AMENDMENT-6 retires `support_digest`,
`clarifications` and `parked_messages` from `CasePanelView`. It was ruled and
never executed. RV sustained it blocking under rule 2 (contract drift) as `E1`
in `.plan/reviews/ACC4-1.md`, escalated to the orchestrator rather than charged
to ACC4.

**Environment.** The only venv is installed editable against the **main**
worktree's `src` via a plain-path `.pth`, so a bare interpreter call from this
worktree imports whatever branch the main tree is on. Every Python command in
this ledger therefore sets
`PYTHONPATH=K:\Projects\Ret\rmap-amend6\backend\src`. Step 1 proves the trap and
the fix rather than asserting them.

---

## Step 1 — base, verified by ref

### `git rev-parse HEAD`

```
c8eac86d642a098943c203597f53c85a5f85c2a9
```

*exit 0*

### `git rev-parse --abbrev-ref HEAD`

```
feat/amendment-6
```

*exit 0*

### `git rev-list --left-right --count refs/heads/refactor/unified-return-platform...refs/remotes/origin/refactor/unified-return-platform`

```
354	0
```

*exit 0*

### `git merge-base --is-ancestor refs/heads/refactor/unified-return-platform HEAD`

```
```

*exit 0*

Local `refactor/unified-return-platform` is **354 ahead / 0 behind** `origin`,
so the local ref is the tip; `feat/amendment-6` is cut from it and the ref is an
ancestor of HEAD (exit 0 above).

---

## Step 2 — the editable-install trap, proved and neutralised

`backend/.venv` exists only in the main worktree, and
`site-packages/return_platform_backend.pth` is a **plain path** line pointing at
the main worktree's `src`. `.pth` paths are appended during `site` processing,
which runs *after* `PYTHONPATH`, so `PYTHONPATH` wins. Proved both directions:

### `cat "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Lib\site-packages\return_platform_backend.pth"`

```
K:/Projects/Ret/returns_muti_agentic_platform/backend/src

```

*exit 0*

### `"K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -c "import return_platform; print(return_platform.__file__)"`

```
K:\Projects\Ret\returns_muti_agentic_platform\backend\src\return_platform\__init__.py
```

*exit 0*

### `PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -c "import return_platform; print(return_platform.__file__)"`

```
K:\Projects\Ret\rmap-amend6\backend\src\return_platform\__init__.py
```

*exit 0*

The bare call imports the **main worktree**; the `PYTHONPATH` call imports
**this** worktree. Two false failures today came from the first line. Every
Python command below sets it.

`frontend/scripts/export-contracts.js` computes the interpreter as
`<its own worktree>/backend/.venv/...`, which does not exist here, so the
regeneration step in step 6 passes `RETURN_PLATFORM_PYTHON` explicitly as well.

---

## Step 3 — baselines, before any change

`backend/tests/conftest.py::pytest_configure` raises without a repository-root
`.env`, which is gitignored and untracked. `checks.yml:213` copies
`.env.example`; done identically here.

### `cd backend && PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -m pytest tests --collect-only -q 2>&1 | tail -1`

```
5251/5765 tests collected (514 deselected) in 6.88s
```

*exit 0*

### `cd frontend && npm test -- --maxWorkers=2 --reporter=default 2>&1 | grep -E "^ *(Test Files|Tests) "`

```
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
```

*exit 1*

### `python -c "import json;print(json.load(open(\"scripts/ci/suite_size_floor.json\"))[\"suites\"])"`

```
{'backend': {'cases': 5251, 'files': 441}, 'frontend': {'cases': 860, 'files': 61}}
```

*exit 0*

**Baseline.** Backend 5251 collected (floor 5251). Frontend 62 files / 867
cases, 865 passed / 2 failed — the two pre-existing allowlisted
`registry.test.ts` failures (FE-DEFECT-2), reproduced identically to
`.plan/reviews/ACC4-1.md` §1. Frontend floor is 61 files / 860 cases; the suite
sits 7 cases above it, well inside `RESTAKE_ALLOWANCE = 0.25`.

The frontend suite exits 1 on those two, which is the tolerated path in
`checks.yml`; the verdict is `assert_known_failures.py`'s.

---

## Step 4 — who writes these fields, and who reads them

The amendment was ruled on 2026-08-31 and may have been overtaken. Two
questions, answered from source before anything is deleted:
**(a) has any of the three acquired a writer?** **(b) who consumes them?**

### (a) Writers

`CasePanelView` is constructed at exactly one site, and every registry
contributor registration in the repository:

### `git grep -n "CasePanelView(" -- backend/`

```
backend/src/return_platform/api/case_panel.py:100:    return CasePanelView(
backend/src/return_platform/operations/case_panel.py:194:class CasePanelView(_Panel):
```

*exit 0*

### `git grep -n "register_panel_section(" -- backend/ | grep -v "def register_panel_section"`

```
backend/tests/api/test_case_panel_and_reviews.py:513:        register_panel_section("tick", counting)
backend/tests/api/test_case_panel_and_reviews.py:539:    register_panel_section("ingress", contribute)
backend/tests/api/test_case_panel_and_reviews.py:562:    register_panel_section("zulu", one)
backend/tests/api/test_case_panel_and_reviews.py:563:    register_panel_section("alpha", two)
backend/tests/api/test_case_panel_and_reviews.py:581:    register_panel_section("relay", broken)
backend/tests/api/test_case_panel_and_reviews.py:598:    register_panel_section("dup", contribute)
backend/tests/api/test_case_panel_and_reviews.py:600:        register_panel_section("dup", contribute)
backend/tests/api/test_case_panel_and_reviews.py:1478:    register_panel_section("guarded", refuses)
backend/tests/api/test_case_panel_and_reviews.py:1494:    register_panel_section("relay", breaks)
```

*exit 0*

### `git grep -n "PANEL_SECTION_IDS: " -- backend/`

```
backend/src/return_platform/operations/case_panel.py:239:PANEL_SECTION_IDS: Final[tuple[str, ...]] = ()
```

*exit 0*

**Every one of the nine `register_panel_section` calls is in a test file.** No
production module registers a section at all, and `PANEL_SECTION_IDS` is the
empty tuple. So the registry has zero production contributors, and the single
`CasePanelView(...)` construction hardcodes all three fields as literals.

**Answer to (a): none of the three has acquired a writer.** The amendment is not
overtaken in any part, and all three are retired. Had one of them gained a
producer I would have left it and reported it; that is not the case here, and
the check was run rather than assumed.

### (b) Readers

### `git grep -nE "\.(support_digest|clarifications|parked_messages)\b" -- frontend/src | grep -v "/mocks/" | grep -vE "\.test\.(ts|tsx)"`

```
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts:34: * `panel.clarifications` is empty on every real panel and will stay empty until
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts:37: * The first draft of this file read **only** `panel.clarifications`, and would
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts:117: * Section payload first, then anything on `panel.clarifications` the section did
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts:128:  const held: unknown = payload?.clarifications;
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts:132:  for (const raw of [...fromSection, ...panel.clarifications]) {
frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.ts:499: * This read `CasePanelView.support_digest` as a fallback until the amendment.
frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.ts:557: * `CasePanelView.parked_messages` is hardcoded `0` and no contributor can change
frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.ts:558: * it, so a `?? panel.parked_messages` fallback would resolve to zero on every
```

*exit 0*

### `git grep -nE "(support_digest|parked_messages)" -- backend/tests`

```
```

*exit 1*

### `git grep -nE "clarifications" -- backend/tests/api/test_case_panel_and_reviews.py`

```
```

*exit 1*

### `git grep -lnE "(support_digest|parked_messages|clarifications)" -- frontend/src | sort`

```
frontend/src/api/caseClarifications.ts
frontend/src/api/generated/return-platform.d.ts
frontend/src/api/graphSchema.ts
frontend/src/domains/returns/ReturnCopilotFabrication.test.ts
frontend/src/domains/returns/panes/casePanel/CasePanel.test.tsx
frontend/src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx
frontend/src/domains/returns/panes/casePanel/sections/ClarificationsSection.test.tsx
frontend/src/domains/returns/panes/casePanel/sections/ClarificationsSection.tsx
frontend/src/domains/returns/panes/casePanel/sections/SupportReplyReview.test.tsx
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.test.ts
frontend/src/domains/returns/panes/casePanel/sections/clarificationModel.ts
frontend/src/domains/returns/panes/casePanel/support/supportPanelIntegration.test.tsx
frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.test.ts
frontend/src/domains/returns/panes/casePanel/support/supportPanelPayloads.ts
frontend/src/domains/returns/panes/casePanel/support/supportSections.test.tsx
frontend/src/mocks/handlers/canonicalHandlers.contract.test.ts
frontend/src/mocks/handlers/canonicalHandlers.ts
frontend/src/mocks/handlers/caseClarifications.contract.test.ts
frontend/src/mocks/handlers/casePanelHandlers.contract.test.ts
frontend/src/mocks/handlers/casePanelHandlers.ts
frontend/src/mocks/handlers/supportHandlers.contract.test.ts
frontend/src/mocks/handlers/supportHandlers.ts
```

*exit 0*

Most files in that last list match on an unrelated `clarifications` — the answer
endpoint path, `caseClarifications.ts`, the *section id* `"clarifications"`, and
V2's `"support_parked_messages"` **section**. Narrowed to the three **DTO
fields**, the consumer set is:

| consumer | field(s) | what it does | disposition |
| --- | --- | --- | --- |
| `operations/case_panel.py:205-208` | all three | declares them | **remove** |
| `api/case_panel.py:105-115` | all three | hardcodes empty + the V1 comment AMENDMENT-6 quotes | **remove, comment included** |
| `frontend/src/.../sections/clarificationModel.ts:132` | `clarifications` | **the one real production read**: `[...fromSection, ...panel.clarifications]` | **migrate** — drop the second vehicle, keep the section vehicle |
| `frontend/src/mocks/handlers/casePanelHandlers.ts:216,217,224` | all three | MSW panel body | **remove** (held to the document by `schemaConformance`'s `additionalProperties: false`) |
| 6 test files building panel fixtures | all three | fixture keys only, never asserted | **remove the keys** |
| 4 × `*openapi*.json`, `generated/return-platform.d.ts` | all three | generated | **regenerate** |

`support_digest` and `parked_messages` have **zero readers anywhere** — the only
non-fixture mentions are two comments in `supportPanelPayloads.ts` recording
that a fallback to them was deliberately *not* written. Backend tests reference
none of the three (`git grep` over `backend/tests` exits 1 for
`support_digest|parked_messages`, and `test_case_panel_and_reviews.py` never
mentions `clarifications`).

So there is exactly **one** breaking consumer, and it is the one V3's own review
(`.plan/reviews/V3f-1.md:316`) said to fix in the same commit as the DTO change.

---

## Step 5 — the change, and what it deliberately is not

Removed:

- `operations/case_panel.py` — the three field declarations.
- `api/case_panel.py` — the three literals and the V1 comment AMENDMENT-6
  quotes.
- `mocks/handlers/casePanelHandlers.ts` — the three mock keys.
- five test files — the three fixture keys each. Fixture keys only; none was
  ever asserted on.

Migrated, not removed:

- `sections/clarificationModel.ts` — `[...fromSection, ...panel.clarifications]`
  becomes `fromSection`. The de-duplication and the order guarantee stay; they
  now describe one payload naming an id twice rather than two vehicles
  disagreeing.

**Not removed, and this is the part that needed the check:** nothing. All three
fields were verified unwritable in step 4 before any of this. Had one acquired a
producer it would have stayed and been reported instead.

**No test was deleted, skipped, xfailed or weakened.** Three tests in
`clarificationModel.test.ts` had the retired vehicle as their subject. They are
re-pointed:

| before | after |
| --- | --- |
| "still reads the DTO field, in case the integration pass wires that instead" | "draws nothing from a top-level clarifications field, retired by AMENDMENT-6" — the assertion is **inverted**, against a panel body that still carries the retired key |
| "draws one card when both vehicles carry the same clarification" | "draws one card when the section names the same clarification twice" |
| "keeps the section's order and appends what only the field holds" | "keeps the section's own order, and the first mention fixes an id's place" |

The first is the reason `panel` stays in `readClarifications`' signature unread:
drop the parameter and that assertion becomes unwritable, leaving the amendment
with no watcher on the console side.

---

## Step 6 — verification

### `cd frontend && npm run typecheck`

```

> return-platform-console@0.1.0 typecheck
> tsc -b --pretty false

```

*exit 0*

### `cd frontend && npm run lint`

```

> return-platform-console@0.1.0 lint
> eslint . --max-warnings=0

```

*exit 0*

### `cd frontend && RETURN_PLATFORM_PYTHON="K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" npm run contracts:check 2>&1 | tail -6`

```
🚀 openapi/return-platform.openapi.json → src/api/generated/return-platform.d.ts [490.9ms]

> return-platform-console@0.1.0 contracts:served
> node scripts/check-served-fields.js

Fully-required schemas verified against the published document: CaseFactProjection (11)
```

*exit 0*

### `PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" scripts/check_openapi_drift.py 2>&1 | tail -12`

```
  "finished_at": "2026-08-31T15:50:31.310354+00:00",
  "openapi_sha256": "14c757333aa29438a9fb2b7868bc3a5756c9386412033f72f309bb786be424f9",
  "snapshots": [
    "openapi/return-platform.openapi.json",
    "backend/openapi/return-platform.openapi.json",
    "frontend/openapi/return-platform.openapi.json",
    "openapi.json"
  ],
  "diffs": [],
  "status": "PASS",
  "exit_code": 0
}
```

*exit 0*

`contracts:check` regenerates from the live FastAPI app and then
`git diff --exit-code`s the document and the generated types: exit 0 means the
committed artifacts *are* what this backend serves. `check_openapi_drift.py`
confirms all four published copies are the same bytes.

### The suites, after the change

### `tail -1 backend/pytest-after.log`

```
1 failed, 5240 passed, 10 skipped, 514 deselected, 2 warnings in 270.90s (0:04:30)
```

*exit 0*

### `cd backend && python ../scripts/ci/assert_known_failures.py --suite backend --report junit-backend.xml`

```
suite size held: 441 test files/modules, 5251 test cases (floor 441 / 5251)
5251 tests ran, 1 failed, 1 allowlisted
only the 1 known, still-failing tests failed
```

*exit 0*

### `cd frontend && python ../scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend.xml`

```
suite size held: 62 test files/modules, 867 test cases (floor 61 / 860)
858 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
```

*exit 0*

### `cd frontend && npm test -- --maxWorkers=2 --reporter=default 2>&1 | grep -E "^ *(Test Files|Tests) "`

```
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
```

*exit 1*

### Before / after

| suite | before | after | floor |
| --- | --- | --- | --- |
| backend | 5251 collected; 5240 passed, 1 failed, 10 skipped | 5251 collected; 5240 passed, 1 failed, 10 skipped | 441 files / 5251 cases — **held exactly** |
| frontend | 62 files / 867 cases; 865 passed, 2 failed | 62 files / 867 cases; 865 passed, 2 failed | 61 files / 860 cases — held |

Both failures are the pre-existing allowlisted ones and neither is touched by
this change: backend
`tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item`
(`'_Runtime' object has no attribute 'patched'`) and the frontend
`registry.test.ts` `/shipments` pair. `assert_known_failures.py` reports
"only the N known, still-failing tests failed" on both suites.

**The size floor is not restaked, deliberately.** Collected counts are identical
before and after on both suites, because no test was removed — the three tests
whose subject was the retired vehicle were re-pointed at the surviving one. A
floor edit here would be a change with nothing behind it. `_check_size` prints
`suite size held` on both, and the frontend's 867 against a floor of 860 is well
inside `RESTAKE_ALLOWANCE = 0.25`, so no re-stake is due on growth either.

### Gates that run what this change adds (rule 13)

| added | gate |
| --- | --- |
| the retirement guard in `clarificationModel.test.ts` | `frontend-tests` job, `checks.yml:559` |
| the migrated `readClarifications` | `frontend-static` (lint + typecheck) |
| the regenerated document and types | `contracts` job, `checks.yml:630` (`npm run contracts:check`) |
| the mock body losing three keys | `contracts` — `schemaConformance`'s `additionalProperties: false` on `CasePanelView` makes re-adding one a red test |

`scripts/dev/ledger_capture.sh` is a ledger-writing helper, not a guard, and
gates nothing by design.

---

## Step 7 — the records that asserted the old state

`.plan/merge.md:107` and `.plan/acceptance/STATUS.md` finding 5 both stated, in
the present tense, that the three fields are still on the DTO and in the
published document. That is now false, and a tracking record that is false in
the direction of "unfinished work remains" is how the amendment got lost the
first time. Both are marked executed/closed, with the writer check and the
suite figures recorded inline so the next reader does not have to take it on
trust.

Left alone deliberately: `.plan/handoffs/V1-phase2.md:91-92` and
`.plan/handoffs/V3-frontend.md:15-18`, which contain the original (wrong) claim
that the fields "arrive through the section registry". Those are historical
handoff documents describing what was believed at the time; AMENDMENT-6 in
`contracts.md` §1a is the governing record and already contradicts them.
Rewriting history there would erase the evidence of how the defect happened.

`.plan/contracts.md` §9 (line 111) already reads "per AMENDMENT-6,
`support_digest`, `clarifications` and `parked_messages` are retired" — the
contract was correct all along; only the code lagged. No contract edit was
needed and none was made.

## Open / not closed

- **`.plan/reviews/ACC4-1.md` does not exist at trunk.** It is reachable only as
  `git show 9b1901fc:.plan/reviews/ACC4-1.md`, on a commit not in
  `refactor/unified-return-platform`. The review was read from there. Whoever
  owns the review record should land it on trunk; this branch did not, because
  RV owns `.plan/reviews/` and E1 was escalated rather than assigned to a slice.
- **E2 (FE-DEFECT-5, the axe sweep no workflow runs)** is untouched. Different
  owner (`checks.yml`), different finding.
- The two pre-existing allowlisted failures are untouched and remain owned
  elsewhere.

---

## Step 8 — the tip moved mid-flight; rebased and re-measured

Trunk advanced from `c8eac86d` to `72f37ba2` while this work was in progress
(the RUNTIME slice: `_Runtime.patched`, and the defect it was hiding). Checked
before rebasing rather than after: the nine new commits touch
`.plan/reviews/RUNTIME-1.md`, `.plan/tracks/RUNTIME.ledger.md`,
`backend/tests/test_cumulative_support_outcomes.py` and
`scripts/ci/known_test_failures.json` — **no overlap with this branch's files**.
Rebase was clean.

It does change the backend baseline: RUNTIME fixed the failure this branch
measured against and **deleted the backend allowlist entry**, so
`known_test_failures.json`'s backend list is now empty and the backend suite is
expected fully green. The floor is unchanged at 441 / 5251. Everything below is
re-measured on the rebased head, not carried over.

### `git log --oneline -1 && git merge-base HEAD refs/heads/refactor/unified-return-platform`

```
ef02764c docs(amend6): close the finding in the records that still assert the old state
72f37ba2af8c681d2537391ebf24ab063d6a2e63
```

*exit 0*

### `git diff --name-only c8eac86d..refs/heads/refactor/unified-return-platform`

```
.plan/reviews/RUNTIME-1.md
.plan/tracks/RUNTIME.ledger.md
backend/tests/test_cumulative_support_outcomes.py
scripts/ci/known_test_failures.json
```

*exit 0*

### Re-measured on the rebased head

### `tail -1 backend/pytest-rebased.log`

```
5246 passed, 10 skipped, 514 deselected, 2 warnings in 271.60s (0:04:31)
```

*exit 0*

### `cd backend && python ../scripts/ci/assert_known_failures.py --suite backend --report junit-backend.xml`

```
suite size held: 441 test files/modules, 5256 test cases (floor 441 / 5251)
5256 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed
```

*exit 0*

### `cd frontend && python ../scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend.xml`

```
suite size held: 62 test files/modules, 867 test cases (floor 61 / 860)
858 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
```

*exit 0*

### `cd frontend && RETURN_PLATFORM_PYTHON="K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" npm run contracts:check 2>&1 | tail -3`

```
> node scripts/check-served-fields.js

Fully-required schemas verified against the published document: CaseFactProjection (11)
```

*exit 0*

**Final figures, on base `72f37ba2`:**

| suite | collected | result | floor | gate |
| --- | --- | --- | --- | --- |
| backend | 5256 (441 modules) | 5246 passed, 10 skipped, **0 failed** | 441 / 5251 — held | exit 0 |
| frontend | 867 (62 files) | 865 passed, **2 failed** (pre-existing allowlisted `registry.test.ts`) | 61 / 860 — held | exit 0 |

Backend collection rose 5251 → 5256 because RUNTIME added five tests on trunk,
not because of this branch; this branch's own delta is **zero on both suites**.
5256 is inside the floor's `RESTAKE_ALLOWANCE`, so no restake is due from this
branch either. Backend is now fully green — the failure this branch originally
measured against was fixed on trunk mid-flight.

**The floor was not touched.** The instruction was that a change altering
collected counts must restake deliberately; this change does not alter them.


---

## Step 9 — RV round 1: `CHANGES_REQUIRED`, three findings, all documentation-plane

`.plan/reviews/AMEND6-1.md`. Rebased onto the review tip first (`16868eaa`).
All three findings accepted. None touches code that runs.

### F1 — four sentences still asserting the retired fields exist

`supportPanelPayloads.ts:502` and `:557-558` said in the present tense that
`api/case_panel.py` hardcodes `support_digest=()` and that
`CasePanelView.parked_messages` "is hardcoded `0`". Both false as of the
retirement, and RV's framing is the one to keep: **the same shape as the defect
AMENDMENT-6 exists to retire, in the same directory, about the same fields, 26
lines from a comment that already says it correctly.** Fixed in all four places
(two production docstrings, two test comments): past tense for what was true,
and the retirement named. The reasoning each comment exists to give — why there
is no fallback — is preserved intact; only the tense and the now-dangling line
reference changed.

### F2 — a sha that does not resolve, and a superseded figure

Both fixed, and the sha half **not** as literally prescribed. RV suggested
citing `b7e0a529`. That commit was *already unreachable from the branch head by
the time I read the review* — the rebase onto the review tip had moved it to
`a46e858b`, exactly as the earlier rebase had moved `dafd8a07` to `b7e0a529`:

### `git merge-base --is-ancestor dafd8a07 HEAD; echo "dafd8a07 reachable from head: exit $?"`

```
dafd8a07 reachable from head: exit 1
```

*exit 0*

### `git merge-base --is-ancestor b7e0a529 HEAD; echo "b7e0a529 reachable from head: exit $?"`

```
b7e0a529 reachable from head: exit 1
```

*exit 0*

### `git log --format="%h %s" HEAD | grep "execute AMENDMENT-6"`

```
a46e858b refactor(panel)!: execute AMENDMENT-6 -- retire the three unfillable DTO fields
```

*exit 0*

Two shas named by this record in one day, both orphaned by routine rebases onto
a moving trunk; writing a third would re-introduce F2 on the next rebase, and
this branch will rebase again before it merges. So both records now cite the
**branch and the commit subject** — the two things a rebase cannot change —
with a sentence saying why, and pointing at `git log --grep` and the ledger for
resolution after merge. This meets F2's requirement (a pointer that resolves)
rather than its literal suggestion; RV asked to be argued with on evidence, and
the evidence is the two `exit 1`s above.

The figure half is straightforward: both records said "suite sizes unchanged
(backend 5251, frontend 867)". They now say backend **5256** / frontend **867**
at the branch tip, with the reason 5256 is not this branch's doing — RUNTIME's
five tests landing on trunk — stated inline rather than left to be reconciled
against `suite_size_floor.json`.

### F3 — the two handoffs: banners, not rewrites

**RV is right and I was wrong.** My position was history-versus-guidance at the
document level. The closing argument is the one that lands: I had already
banner-corrected `STATUS.md` and `merge.md`, so the distinction actually
operating on this branch was *documents I opened* versus *documents I didn't*,
which has no principle behind it. History and guidance are properties of a
passage.

Dated banners added; **not one wrong sentence was edited or removed.**

- `V1-phase2.md` — at the head of the table literally titled "`CasePanelView`,
  frozen", which is the field inventory a later slice opens to learn what the
  DTO has. Names the three retired rows and says not to read the table as
  current for them.
- `V3-frontend.md` §1 — the passage where somebody *noticed*, preserved whole
  including "**Nothing can fill it**", which was correct and is the amendment's
  provenance. The banner separates the three states RV distinguished: the
  diagnosis (accepted and acted on), "What the console does" (**no longer what
  it does**), and "What is owed on the backend" (**still owed** — no production
  module registers a clarifications section).
- `V3-frontend.md` break-test 3 — annotated in place: it no longer type-checks,
  and the equivalent injection today is named, with its measured result (one
  red, the retirement guard). The original 3-of-14 measurement stays as the
  evidence that produced the amendment.

### Re-verification after the three fixes

F1 touched two test files (comments only). Re-run in full rather than reasoned
about:

### `cd frontend && npm run typecheck`

```

> return-platform-console@0.1.0 typecheck
> tsc -b --pretty false

```

*exit 0*

### `cd frontend && npm run lint`

```

> return-platform-console@0.1.0 lint
> eslint . --max-warnings=0

```

*exit 0*

### `cd frontend && python ../scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend.xml`

```
suite size held: 62 test files/modules, 867 test cases (floor 61 / 860)
858 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
```

*exit 0*

### `cd frontend && RETURN_PLATFORM_PYTHON="K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" npm run contracts:check 2>&1 | tail -3`

```
> node scripts/check-served-fields.js

Fully-required schemas verified against the published document: CaseFactProjection (11)
```

*exit 0*

### `tail -1 backend/pytest-final.log`

```
5246 passed, 10 skipped, 514 deselected, 2 warnings in 277.50s (0:04:37)
```

*exit 0*

### `cd backend && python ../scripts/ci/assert_known_failures.py --suite backend --report junit-backend.xml`

```
suite size held: 441 test files/modules, 5256 test cases (floor 441 / 5251)
5256 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed
```

*exit 0*

Unchanged on both suites: backend 5256 collected / 5246 passed / 10 skipped / 0
failed; frontend 62 files / 867 cases / 865 passed / 2 allowlisted failures.
Both gates exit 0, `contracts:check` exits 0, typecheck and lint clean. The
three fixes were comments and planning documents, and the measurements say so.

## Open / not closed (updated)

- `.plan/reviews/ACC4-1.md` is now on trunk (`bf7fa140`, per RV) — closed by
  someone else's commit; the earlier ledger note stands as the record of why it
  was read by `git show`.
- **E2 (FE-DEFECT-5, the axe sweep no workflow runs)** — untouched, different
  owner (`checks.yml`).
- The two allowlisted `registry.test.ts` failures — pre-existing, owned
  elsewhere.
- **F2's sha citation is answered with an argument rather than the literal
  edit.** If RV wants a sha in those two records despite the two orphanings
  demonstrated above, the merge commit is the only one that will hold, and that
  sha does not exist until this branch merges — so it would have to be written
  by whoever merges it, not here.
