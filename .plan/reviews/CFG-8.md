# RV — CFG-8 @ 990b97d6 — VERDICT: PASS

Base `35f0356c`, branch `feat/cfg-8-policy-screen`, worktree `.claude/worktrees/cfg-8`.
Python `backend/.venv/Scripts/python.exe`, `PYTHONPATH=<worktree>/backend/src`; import proven from the
worktree, not MAIN:

```
K:\...\.claude\worktrees\cfg-8\backend\src\return_platform\__init__.py
K:\...\.claude\worktrees\cfg-8\backend\.venv\Scripts\python.exe
```

No blocking findings. The user's ask is answered literally: one switch at the top of `/config/policy`
(`policy_evaluation.enabled`) whose reason field appears exactly when the model requires it, and
directly beneath it the 30-day window with per-field reset to the packaged value. The preview route
runs the workflow's own evaluator, never a second copy, and reads nothing.

## Findings

| ID | Sev | file:line | What | Why | Fix |
|----|-----|-----------|------|-----|-----|
| A1 | ADVISORY | `frontend/src/domains/config/PolicySection.tsx:640-647` | Precedence is a free `OrderedList` — `FERGUSON_STANDARD_RETURN` is **not pinned last**, and the inline slot `errorMap.get("return_eligibility_policy.precedence")` can never fill: the rule is a model-level validator, so the 422 reports at path `return_eligibility_policy` (pasted below). | The brief asked for it pinned. The safety property holds — the model refuses the release and `ValidationErrors` lists the message — but the operator learns at Validate, from the list, not from the control they just dragged. | Render the `FERGUSON_STANDARD_RETURN` row non-movable (or key the inline error off `return_eligibility_policy` as well, so the message lands on the list that caused it). |
| A2 | ADVISORY | `frontend/src/domains/config/PolicySection.tsx:766` | The Evaluate button carries `text-on-surface-variant … disabled:opacity-40` — the exact token pair CFG-5 F1 measured at **2.048:1** and removed at the cause in `UndecidedKeysPanel.tsx:175`. | Mitigating and why this is not blocking: the disabled state here is transient (`preview.isPending` only, not a persistent gate like the packaged-adoption button), and the identical class string still sits untouched in `PublishBar.tsx:55` and `KeyValueTable.tsx:232`, i.e. this lease matched the house pattern rather than regressing a fixed one. It is still the pattern CFG-5 H1 asked to stop spelling. | `disabled:border-outline-variant` + `disabled:cursor-not-allowed`, no opacity on the foreground. |
| A3 | ADVISORY | `PolicySection.tsx:651` inside `TypedSectionScreen.tsx:249` | Block 7 (the preview panel) is rendered inside the `<fieldset disabled={!canWrite}>`, so a read-only operator cannot click Evaluate. | The route was deliberately scoped to read roles — `router.py:78-81`: "an operator previewing a draft they have not published yet must not need the write capability a publish would". The screen then withholds it from exactly that principal. `PolicySection.test.tsx:264-272` pins the fieldset behaviour but never asserts the preview panel's own reachability. | Render `<PolicyPreviewPanel>` as a sibling of the fieldset (it writes nothing into the draft), and add a test that Evaluate is enabled with `config.runtime.read` alone. |
| A4 | ADVISORY | `PolicySection.tsx:41-45, 284-285` (and reused at `569, 576, 583, 590`) | `ELIGIBILITY_DECISIONS` offers all three `EligibilityDecision` members to every decision field, including the ones the model's validators always refuse — REJECT on `decision_when_satisfied` (`eligibility_policy.py:143`), APPROVE on three of the four `special_or_nonstock.decisions`. | "Options only from the model" is satisfied — but an option that can only ever 422 is a trap the hint mitigates rather than removes. `outside_standard_window` (line 47-50) does this correctly by filtering APPROVE out. | Filter per field, the way `OUTSIDE_WINDOW_DECISIONS` already does. |
| A5 | ADVISORY | `PolicySection.tsx:793-798` | The preview's "Return reason" is free text, while `RETURN_REASON_SUGGESTIONS` (line 81-94 — the model's `ReturnReason` minus `UNKNOWN`) is already in the same file and used for the two `TagListInput`s below. | A typo becomes a 422 round-trip in a panel whose whole point is a fast answer. | `EnumSelect` over the same constant, with a "not stated" empty option. |
| A6 | ADVISORY | `PolicySection.tsx:151-157, 244-248` | When `GET /api/config/packaged/RETURN_PLATFORM` fails, `packaged.data` stays `undefined` and every reset link silently disappears with no notice. This is the live state today: the running backend is trunk head and 404s the route. | Deliberate and commented, and the right failure direction. But "no link" is indistinguishable from "the value already matches packaged" — the two states an operator most needs to tell apart on this screen. | One line under block 2 when `packaged.isError`: "Packaged defaults are unavailable; reset links are hidden." |
| A7 | ADVISORY | `evidence/orchestration/drops/LEASE-CFG-8/drop.json:5` | `"head_sha": "f8c0b6ab"` — stale; HEAD is `990b97d6`, the commit that edited this very file. | The drop is the orchestration record of what is being reviewed. | Update to `990b97d6`. |

## Pasted outputs

**Backend suites** (worktree venv, worktree `PYTHONPATH`):

```
$ pytest tests/api/test_policy_preview.py -q
13 passed, 1 warning in 3.37s

$ pytest tests/api tests/configuration -q
695 passed, 6 deselected, 2 warnings in 81.88s (0:01:21)
```

**The preview route, called directly** (FastAPI TestClient over the worktree router, real packaged
`return_eligibility_policy` from `config/returns`):

```
--- days=10 status=200
  "decision": "APPROVE",
  "route": "STANDARD_RETURN",
  "applied_rules": ["POLICY_RELEASE_VALIDATED","STANDARD_STOCK_ITEM",
                    "CONDITION_FACTS_NOT_EVALUATED","WITHIN_30_DAYS","RESTOCKING_FEE_APPLIES"],
  "conditions": ["RESTOCKING_FEE_APPLIES"],
  "reason_codes": ["WITHIN_STANDARD_RETURN_WINDOW"],
  "policy_evaluation_state": "EVALUATED"

--- days=40 status=200
  "decision": "REVIEW_REQUIRED",
  "applied_rules": [...,"OUTSIDE_STANDARD_WINDOW"],
  "reason_codes": ["OUTSIDE_STANDARD_RETURN_WINDOW"]

--- packaged policy_evaluation (enabled:false) status=200
  "evaluation_enabled": false, "decision": null, "route": null, "applied_rules": [],
  "policy_evaluation_state": "SKIPPED_BY_CONFIGURATION",
  "policy_evaluation_skip_reason": "Suspended on this development host while order-discovery
    turns are answered through the MANUAL provider. Re-enable before any case that is not a
    walkthrough."

--- invalid block status=422
  [{"path": "return_eligibility_policy.version", "message": "Field required", "type": "missing"}, …]

--- purchase_window.days = 0 status=422
  [{"path": "return_eligibility_policy.standard_stock_return.purchase_window.days",
    "message": "Input should be greater than or equal to 1", "type": "greater_than_equal"}]

--- precedence reordered (FERGUSON_STANDARD_RETURN first) status=422   <-- finding A1
  [{"path": "return_eligibility_policy",
    "message": "Value error, the Ferguson standard return is the lowest-priority authority and
      must be last in the precedence chain", "type": "value_error"}]
```

The `SKIPPED_BY_CONFIGURATION` correction is right and the brief's draft copy was wrong:
`return_case_activities.py:1021-1031` writes `policy_evaluation_state =
PolicyGateState.SKIPPED_BY_CONFIGURATION` plus `policy_evaluation_skip_reason`, with no route and
no decision. `policy_preview.py:36` imports that enum read-only rather than restating the literal,
and the screen's off-state sentence (`PolicySection.tsx:221`) quotes both fact names.
`CONDITION_FACTS_NOT_EVALUATED` is a different mechanism — an *enabled* gate over unstated facts —
and appears only where it belongs, in the `unstated_condition_facts` hint (line 308).

**Frontend:**

```
$ npx vitest run
Test Files  90 passed (90)
     Tests  1083 passed (1083)

$ npm run typecheck   → tsc -b --pretty false, exit 0, no output
$ npm run lint        → eslint . --max-warnings=0, exit 0, no output
```

**Contract copies** — four files, one sha256 (first 16 chars), and the drift check agrees:

```
openapi.json                                  a929336825b477ea
openapi/return-platform.openapi.json          a929336825b477ea
backend/openapi/return-platform.openapi.json  a929336825b477ea
frontend/openapi/return-platform.openapi.json a929336825b477ea

$ python scripts/check_openapi_drift.py
"openapi_sha256": "a929336825b477ea…", "diffs": [], "status": "PASS", "exit_code": 0
```

**The publish path, live, once.** Disposable `vite --port 5195` from this worktree proxying `/api`
to the already-running `:8000` (never restarted), `--workers=1`. The committed
`e2e/config-policy.spec.ts` cannot complete against that backend — it is trunk head and
`/openapi.json` confirms `policy/preview: False, packaged/{domain_key}: False` — and failing at its
preview step would abandon the release at 45 days, so I ran a disposable copy of the same file with
the preview block removed, then deleted it (`git status --porcelain` clean afterwards).

```
RUNTIME BEFORE   release_id publish-57674a743f1d4a92  head_revision 146
                 purchase_window {"days": 30, "basis": "PURCHASE_DATE"}

ok 1 [cfg4-e2e] › rv-cfg8-publish.spec.ts › widens the return window, publishes, then reverts (7.1s)
   RUNTIME AFTER CHANGE days = 45
   RUNTIME AFTER REVERT days = 30
1 passed (10.5s)

RUNTIME AFTER    release_id publish-a47bf2f938c74394  head_revision 148
                 purchase_window {"days": 30, "basis": "PURCHASE_DATE"}
```

Reverted; head advanced by exactly the two publishes the run made.

**Will the committed spec's preview assertions pass after merge?** Yes, on the live release as it
stands. I read it: `policy_evaluation {"enabled": true, "disabled_reason": null}` and
`unstated_condition_facts: REVIEW_REQUIRED`. The spec's own note is therefore accurate, and its
choice to answer all thirteen facts explicitly is what makes line 110 depend on the window rather
than on silence. At 40 days inside the published 45-day window, with every fact satisfied, the
evaluator returns APPROVE — the same path my `days=10` call above took. One fragility worth naming,
not a change I would require: the assertion also depends on the live release keeping
`policy_evaluation.enabled: true`. The packaged file ships `enabled: false`, so a stack rebuilt
straight from `return_policy.yaml` would make the panel answer "Policy evaluation is off" and line
110 would fail for a reason the spec never mentions. A one-line guard (assert the runtime's
`policy_evaluation.enabled` before the preview block, skip with that reason otherwise) would make
the failure legible.

## Judgement

This lease does the thing the user asked for and does not do anything else. The evaluator and the
workflow are untouched — `git diff --name-only 35f0356c..HEAD -- backend/src/return_platform/policy/
backend/src/return_platform/workflows/` is empty — and the preview route earns its existence by
delegating to `evaluate_return_eligibility` rather than restating it, with the import list of
`policy_preview.py` carrying no repository, graph or Mongo name at all; the one place the workflow's
own vocabulary was needed, the disabled-gate answer, is imported rather than spelled, and the
implementer's correction of the brief's `CONDITION_FACTS_NOT_EVALUATED` copy to
`SKIPPED_BY_CONFIGURATION` is the single best decision in the lease — it is the difference between
"no rule was applied" and "a rule approved this", and the screen now says the true one. The two
outside-Owns files are justified: `test_canonical_config_api.py` names the new POST by exception
with the same reasoning `/validate` already carried rather than widening the assertion, and the
drift receipt is generated. Everything the drop claims reproduced on my own runs, including the
live publish path, which I ran and reverted with the runtime pasted either side. What is left is
seven advisories, and the shape of them is consistent: the screen is honest about the model's rules
in prose — REJECT is refused here, APPROVE is refused there, the Ferguson entry must be last — in
several places where it could have been honest in the control instead (A1, A4), and the preview
panel, which the backend deliberately opened to read roles, is fenced behind the write capability by
the chrome it sits inside (A3). None of those can publish a bad release; the models refuse every one
of them, and the 422 paths map. PASS.
