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

