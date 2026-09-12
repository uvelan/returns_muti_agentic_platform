# RV — CFG-7 @ b079a987 — VERDICT: CHANGES_REQUIRED

Base `261dc3bc`, head `b079a987`, branch `feat/cfg-7-acceptance`, worktree `.claude/worktrees/cfg-7`.
`python -c "import return_platform; print(return_platform.__file__)"` →
`…\.claude\worktrees\cfg-7\backend\src\return_platform\__init__.py` (not MAIN's checkout).
Read: `CFG-7.brief.md`, `CFG.brief.md` §6, ledger steps 00–13, `drops/LEASE-CFG-7/drop.json`,
`.plan/acceptance/config-screens.md`, `FINAL_REPORT.md` §L, `git log --stat 261dc3bc..HEAD` and every diff.

Two blocking findings, both about the **record** rather than the code: three fixes the brief names by
ID were dropped without a line anywhere, and the definition-of-done verdict rests on a claim about
`backend/config/seed/` that is false. Everything else this lease claims, I reproduced.

## Findings

| ID | Sev | Where | What | Why it matters | Fix |
|---|---|---|---|---|---|
| **F1** | **BLOCKING** | `frontend/src/domains/config/PolicySection.tsx:786`; `frontend/src/domains/config/UndecidedKeysPanel.tsx:188`; `frontend/src/domains/config/OverviewSection.test.tsx:272` | Brief item 2 names four fixes by ID. **CFG-8 A2** (`disabled:opacity-40` on `text-on-surface-variant`), **CFG-5 H1** (disabled button pixel-identical to enabled) and **CFG-5 H2** (the test pins the `opacity-\d` *spelling*, not the property) are unchanged, and the strings "A2", "H1", "H2" appear nowhere in CFG-7's ledger (steps 00–13), `drop.json` or `config-screens.md`. Only H5's reflow is disposed of. I measured A2 at **2.046:1** against the live token values — the same defect, at the same number, that CFG-8 A2 quoted as 2.048:1. | `drop.json` records `2_axe_sweep: "DONE"`, and item 3's own rule is "each closed with a test or a ledger reason". Three named items got neither. Axe does not flag disabled controls (1.4.3 exempts them), so a clean sweep is not evidence about any of the three — the sweep passing is exactly why the omission is invisible. | Either change `disabled:opacity-40` → the `disabled:hover:*` treatment `UndecidedKeysPanel.tsx:188` already uses, widen `OverviewSection.test.tsx:272` to also assert no `disabled:text-*` override, and answer H1's "dimming was right, the *how* was wrong"; **or** record a reason per ID and downgrade `2_axe_sweep` from DONE. Not both silent. |
| **F2** | **BLOCKING** | `.plan/tracks/CFG.ledger.md` step:12 item 2; `drop.json.remaining[3]`; `FINAL_REPORT.md` §L context | The definition-of-done item-2 verdict says `data_platform/` and `seed/` "are not loaded by any runtime path, per the directory's own README". **False for `seed/`.** `operations/seed_manifest.py:35` computes `SEED_MANIFEST_PATH` from `backend/config/seed/e2e_seed_manifest.json` and `:57` reads it **at module import** (`_SEED_CONFIGURATION: Final = _load_seed_configuration()`); `_seed_manifest_path()` raises `FileNotFoundError` when the file is absent. `main.py:51` imports `api/order_lines.py`, which imports it at `:95` — as do `operations/repository.py:66` and `workflows/case_customer_identity.py:54`. Delete that file and the app does not start. | The ledger quotes the README's *intent* sentence ("fixtures for local/dev seeding, not production runtime configuration") as if it settled the *load-path* question. It does not, and this is the one line the track closes item 2 on. Left standing it reads as an invitation to delete a startup-critical file. | Correct the step:12 line: `seed/e2e_seed_manifest.json` **is** runtime-loaded (import-time) and satisfies item 2; `seed/generation.yaml` (only `backend/scripts/generate_seed_data.py:128`) and `data_platform/*.yaml` (only `data_platform/graph/sandbox_runner.py:68`'s CLI default, plus tests) do not. See §DoD item 2 below for the recommended closure. |
| A1 | ADVISORY | `frontend/src/domains/ai/AiControlCenterPage.tsx:1691` | The local `Switch` keeps the exact geometry step:08 removed from `Toggle`: `className="peer sr-only"` behind two `absolute inset-0` decorative spans. | Not the same *defect* — here the `<label>` (`:1687`) already wraps the graphic, so a real mouse click forwards natively and no user is blocked. But the input is still not hit-testable where it is drawn, so `getByRole("checkbox").click()` will intercept exactly as `config-agents.spec.ts` did — on the two screens (`/ai/configuration`, `/ai/providers-models`) this lease left without a live spec. The next person to write those specs hits it first. | Same two-line change as `Toggle.tsx:69`: `absolute inset-0 size-full appearance-none opacity-0`. |
| A2 | ADVISORY | `docs/evidence/stage4o_complete_audit/generate_audit_artifacts.py:106,108,114,120,124,149,151,219` | `backend/config/returns/production.yaml` and `backend/config/ai_gateway.yaml` — both deleted by CFG-2 — are still named in eight `feature(...)` rows and the agent matrix. Only the two `configuration_matrix` rows were repointed. | The brief scoped the ask to `:264,266`, so the letter is met and this is not blocking. The file as a whole still generates a report naming files that do not exist. | Same repoint, eight more call sites, or one shared constant. |
| A3 | ADVISORY | `frontend/src/components/forms/OrderedList.tsx:29-36,75` | `fixedTrailing` is a predicate over *every* item, not an assertion about the last position. A draft whose `FERGUSON_STANDARD_RETURN` is not last renders "Fixed last" mid-list and freezes both neighbours. | Unreachable through the typed control (the loaded release always validates), reachable through Advanced/JSON mode — and Advanced mode is also the escape hatch, so nobody is stuck. Worth a line in the prop doc. | Note the precondition in the JSDoc, or refuse the label when `index !== items.length - 1`. |
| A4 | ADVISORY | `evidence/orchestration/drops/LEASE-CFG-7/drop.json:5` | `head_sha: "d2eddd76"`; the branch head is `b079a987` (the drop commit itself). | Self-referential and harmless, but the drop is the merge record. | `b079a987`. |
| A5 | ADVISORY | `scripts/ci/suite_size_floor.json` (`frontend.cases: 1092`) | Measured at step:06; step:08 added the Toggle regression test, so the head runs 1093. | Floor semantics make this safe (growth never fails), but the file's own "HOW THESE WERE MEASURED" block claims the measurement is at this lease's head. | Re-stake to 1093, or date the measurement to step:06. |
| A6 | ADVISORY | brief "Owns" vs scope items 3 and 4 | `backend/tests/test_configuration_api.py` (+39) and `frontend/src/main.tsx` (the `onUnhandledRequest` line) are outside Owns as written, and demanded verbatim by items 3 (CFG-6 A6's HTTP-layer test) and 4 (`"error"` restored). | The implementer resolved the contradiction the right way — the specific instruction beats the general list, and both diffs are exactly the named change and nothing else. Recorded so the merge does not read as scope creep. | None; note it in the merge record. |
| A7 | ADVISORY | `evidence/config_audit/FINAL_REPORT.md` §L "Net" line | "15 of 20 fixed and holding (01–10, 13, 15–18)" counts DEF-06 in the 15, while DEF-06's own row says "superseded, not regressed". | Arithmetic still totals 20; only the label disagrees with itself. | "(01–05, 07–10, 13, 15–18, plus 06 superseded)". |

## Pasted outputs (all re-run by RV on `b079a987`)

```
$ backend/.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider \
    --ignore=tests/configuration/test_concurrent_activation.py -rf
42 failed, 5394 passed, 10 skipped, 516 deselected, 2 warnings in 554.96s (0:09:14)

$ diff <the 42 FAILED ids, normalised> <known_test_failures.json suites.backend.known_failures>
IDENTICAL: the 42 ids that failed == the 42 registered (42/42)

$ python scripts/ci/assert_known_failures.py --suite backend --report junit-backend.xml
suite size held: 455 test files/modules, 5446 test cases (floor 455 / 5446)
5446 tests ran, 42 failed, 42 allowlisted
only the 42 known, still-failing tests failed                            [[ exit 0 ]]

$ python scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend.xml
suite size held: 90 test files/modules, 1093 test cases (floor 90 / 1092)
1082 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed                            [[ exit 0 ]]

$ python scripts/ci/test_assert_known_failures.py
all negative controls passed                                            [[ exit 0 ]]

$ npx vitest run
 Test Files  90 passed (90)
      Tests  1093 passed (1093)
$ npm run typecheck  -> exit 0      $ npm run lint  -> exit 0

$ python scripts/check_openapi_drift.py
"commit": "b079a987...", "diffs": [], "status": "PASS", "exit_code": 0
```

Axe / route sweep, disposable `vite --mode mock --port 5178 --strictPort`, `main.tsx` at
`onUnhandledRequest: "error"`, `--workers=1` (`:5174` and `:5173` untouched):

```
$ E2E_BASE_URL=http://localhost:5178 npx playwright test --project=mock-chromium \
    tests/canonical-routes.spec.ts --workers=1 --reporter=list
  ok 103  accessibility › /config/overview has no critical or serious violation (1.8s)
  ok 108  accessibility › /config/policy has no critical or serious violation (2.2s)
  ok 110  accessibility › /config/deployment has no critical or serious violation (2.1s)
  ok 130  accessibility › /ai/providers-models has no critical or serious violation (2.3s)
  ok 133  accessibility › /ai/configuration has no critical or serious violation (1.8s)
  ok  89..94  every canonical route reflows › 320 / 390 / 640-zoom200 / 768 / 1280 / 1440
141 passed (5.8m)          # 44 accessibility, 0 failed, 0 flaked -- cleaner than step:02's own run
```

Disabled-control contrast, computed in that same page against the resolved tokens
(`on-surface-variant` `rgb(62,73,71)`, `surface-container-lowest` `rgb(255,255,255)`,
`outline-control` `rgb(130,141,138)`), WCAG relative-luminance formula:

```
enabled text on white ........................................ 9.338 : 1
opacity-40 text on white  (PolicySection.tsx:786, Evaluate) ... 2.046 : 1   <-- F1, CFG-8 A2 unchanged
fieldset disabled:opacity-75 (TypedSectionScreen.tsx:266) ..... 4.628 : 1   ok
/config/overview "Take packaged file" disabled ............... 9.338 : 1   colour fixed (CFG-5 F1)
   ... and identical to its own enabled state in colour, background and border  <-- F1, CFG-5 H1 open
outline-control border on white (1.4.11 needs 3:1) ........... 3.428 : 1   ok
```

Live acceptance, two specs re-run by RV through a disposable `vite --port 5179`
(`FRONTEND_BACKEND_TARGET=http://localhost:8000`), `--workers=1`; `:5173`/`:8000` never touched,
`:5178`/`:5179` stopped and confirmed down afterwards, `:8000`/`:5173` still 200:

```
$ E2E_REAL_BASE_URL=http://localhost:5179 npx playwright test --project=cfg4-e2e \
    e2e/config-policy.spec.ts e2e/config-deployment.spec.ts --workers=1 --reporter=list
  ok 1 [cfg4-e2e] config-deployment.spec.ts  flips feedback_learning.enabled ... then reverts (27.0s)
  ok 2 [cfg4-e2e] config-policy.spec.ts      widens the return window, previews, then reverts (8.0s)
  2 passed (42.3s)

GET /api/config/runtime          BEFORE (head 189)          AFTER (head 193)
  environment                      development                development
  policy_evaluation.enabled        True                       True
  …purchase_window.days            30                         30
  deployment.feedback_learning     True                       True
```

Four publishes, 189 → 193, every touched value back where it started. The `/config/deployment`
spec's `getByRole("checkbox", { name: "Feedback learning enabled" }).click()` is the real-browser
proof that step:08's `Toggle` fix holds.

## Answers

**Q1 — Toggle.** Yes, on both counts. The input is now `absolute inset-0 z-10 size-full
appearance-none opacity-0` *inside* the `relative inline-flex h-5 w-9` graphic
(`Toggle.tsx:65-73`), so it is hit-testable at exactly the pixels the switch occupies — that is a
real fix, not a workaround, and my own live `config-deployment` click proves it in Chromium. The
label survives: `<label htmlFor={id}>` (`:64`) now wraps both the graphic and the text span
(`:83`), so the text still forwards natively as a second route in, `getByRole("checkbox", { name:
"Enabled" })` still resolves (four existing tests, all green), Space still toggles, and
`peer-focus-visible:ring-2` still paints because the input remains the first sibling. No
double-fire: per HTML's label activation behaviour, a click whose target *is* the labeled control
does not re-forward. The regression test is honest about what it can and cannot pin. **One other
primitive has the same geometry** — `AiControlCenterPage.tsx:1691` (finding A1); there the label
already wraps the graphic, so no user is blocked, but Playwright will intercept identically.

**Q2 — carried advisories.** CFG-8 **A1** fixed at both ends and correctly reasoned: the backend
rule is a `mode="after"` model validator on `ReturnEligibilityPolicy`
(`policy/eligibility_policy.py:431-446`), so pydantic reports at the parent loc, and
`PolicySection.tsx:646-649` now reads `…precedence ?? return_eligibility_policy`, with the row
rendered non-movable (`fixedTrailing`, two `OrderedList` tests). **A3** fixed structurally, not
cosmetically — `TypedSectionScreen` gained `renderOutsideFieldset` (`:61-76,269-271`), rendered as
a *sibling* of `<fieldset disabled>`, with a test that grants only `config.runtime.read` and
asserts the field disabled **and** Evaluate enabled. **Forward note** closed with an explicit
`policy_evaluation.enabled === true` preflight. **CFG-6 A11** documented and *verified*: I checked
the call sites myself — `releases.py:838-839` gates on `body.domain_key ==
RETURN_PLATFORM_DOMAIN_KEY` only, never on whether the patch touched `deployment`, and passes the
whole merged payload; `:1157` does the same for adopt-packaged; `main.py:560-566`,
`runtime_loader.py:122-130` and `settings.py:930-955` are all where `families.md:103-122` says.
**CFG-5b A5** deferral is reasoned and the reasoning is correct: the fix needs
`configuration/api/releases.py` (named under "must not touch: the release API"),
`operations/repository.py` and two bootstrap adapters, none in Owns, and A5 is not one of the 42 —
and step:05 leaves a concrete fix recipe rather than a shrug. **A6** half-closed with a real
HTTP-layer test and the startup half reasoned (both are inline `try/except` inside a
driver-opening path). **A7**, **H4** done. **CFG-3b A2/A5–A8**: I re-verified all four independently
— `KeyValueTable.tsx:47,224` (list error + new-key `aria-invalid`/`aria-describedby`),
`OrderedList.tsx:21`, `PublishBar.tsx:40,65,70-71`, `router.py:323` — all genuinely closed by later
leases. **Neither fixed nor reasoned: CFG-8 A2, CFG-5 H1, CFG-5 H2** → F1.

**Q3 — mock layer.** `main.tsx:107` is back at `"error"` with the reason on it. All five routes
have handlers and contract rows: `/api/config/adoption`, `/api/shipment-status-catalog`,
`/api/shipments` (+ `/:identifier`, + `POST /:shipmentId/events`) in `canonicalHandlers`;
`/api/rma-tickets` and `/api/v1/return-support/work-items` in `supportHandlers`, whose contract
test now asserts the *whole* registered set rather than filtering. Both "real schema defects" are
real, checked against `openapi.json` myself: `SupportWorkItemStatus`' enum is
`['NEW','ACKNOWLEDGED',…]` with **no `OPEN`**, `SupportWorkItemView.required` includes
`requestSnapshotDigest` under `additionalProperties: false`, and `/api/shipments/{shipment_id}/events`
declares `post` only. Sweep reproduced clean under `"error"` — 141/141, no flake.

**Q4 — axe.** Zero violations, 44/44, pasted above; my run was cleaner than step:02's (the
`/ai/providers-models` keyboard flake did not recur). Reflow clean at 320 too, which closes CFG-5
H5 empirically. The disabled-control spot-check is the one thing the sweep cannot see, and it is
finding F1.

**Q5 — CI truthful.** All 42 registered ids are exactly the 42 that fail on this head — diffed, not
eyeballed. `.plan/reviews/CFG-1.md` does not enumerate the ids (Q6 names the nine modules and the
byte-identical base comparison); the nine modules in the JSON comment match CFG-1 Q6 and F7
exactly. Both `assert_known_failures.py` invocations PASS with exit 0; floors are the measured
counts (backend exact, frontend off by the one test step:08 added — A5). Frontend list empty and
verified empty, not merely left alone.

**Q6 — live acceptance.** The table gives value / runtime observation / reverted / pointer for all
12 rows; every row names a real field and a real assertion. Two specs re-run by me, green, values
reverted, pasted above. The two AI Control Center screens are **not** covered, and the reason given
— both edit live AI-dispatch-governing state (`runtime_integrations.ai_providers`,
`AI_GATEWAY.tasks.*`) through the older four-call `runPublishPipeline`, with a blast radius unlike a
`TagListInput` chip, and no restart allowed to recover — is a real risk argument, not a budget
excuse dressed up as one; it names the call sites and the pattern to copy. I accept it as a
recorded gap. The record is also honest about the first eleven-spec attempt failing on the Toggle
defect rather than hiding it, which is the right instinct.

**Q7 — docs.** `docs/screens/configuration.md`'s Sections table matches `CONFIG_SECTIONS`
(`registry.ts`) exactly — all 17, in registry order, slugs included — and the corrected
dependencies row now distinguishes the release's `deployment` from infrastructure wiring.
`families.md`'s new paragraph is accurate against the call sites (verified above). `docs/README.md`
links `DEFERRED_DESIGN.md`. `generate_audit_artifacts.py`: the two lines the brief names are fixed;
eight other references to the same two deleted files remain → A2.

**Q8 — §L.** All 20 §G ids have a final status, a closing lease and an evidence pointer; I spot-checked
DEF-12, DEF-14, DEF-17 and DEF-19 against the code and found no status that is untrue. DEF-12 is the
strongest entry — a *new* residual gap found while verifying §K's "for free" claim, not a status copied
forward. DEF-19 recorded unresolved rather than guessed closed is the right call. One cosmetic count
mismatch → A7.

## Definition of done (`CFG.brief.md` §6)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Every §G defect fixed or deferred with the ID in the ledger | **Met** | `FINAL_REPORT.md` §L, 20/20 with lease + pointer; ledger step:11 |
| 2 | `backend/config/` holds only files the runtime loads; READMEs match | **Not met** — but for one directory, not two | See below; F2 corrects the stated reason |
| 3 | Every section/unit has a typed screen, each proven once live | **Mostly met** | 12 specs green in one pass (`config-screens.md`); 2 of mine re-run; `AI_GATEWAY`'s two screens exist but have no live publish/revert proof — the one recorded gap |
| 4 | No business value read from env at request time | **Met** (inherited, spot-verified) | CFG-6's `apply_deployment_configuration` hot-adopt; step:12's grep; no CFG-7 change touches it |
| 5 | Suites green, drift green, axe clean | **Met on this head** | every block in "Pasted outputs"; axe 141/141 |

**Item 2, recommended closure (a decision, not a deletion by this lease).** The honest split is
three ways, and the current one-line verdict flattens it: (a) `seed/e2e_seed_manifest.json` **is**
runtime-loaded — at import, by a module three mounted routers pull in — so it already satisfies
item 2 and must not be moved or deleted without a code change (F2); (b) `seed/generation.yaml`
(`backend/scripts/generate_seed_data.py:128`) and `data_platform/*.yaml`
(`data_platform/graph/sandbox_runner.py:68`'s CLI default, plus `backend/tests/**`) are genuinely
not runtime configuration; (c) `manifest.yaml`'s one inert entry and
`dynamic_knowledge/active-schema.example.yaml` are already documented as such. So the track should
close item 2 with a **recorded decision**, not a deletion: either amend §6 item 2 to "contains only
files the runtime or its own tooling loads, split by function, each named in the README" — which
`backend/config/README.md:104-116` already satisfies today — or open a successor lease that owns
`backend/config/**` and moves (b) to `backend/fixtures/` / `backend/tools/config/` with the
loaders repointed. Deleting (b) is not on the table: `backend/tests/**` reads both.

**`scripts/reset_all.ps1` bay-seeding gap — confirmed.** `grep -rn "seed_warehouse_bay_configuration\|
backfill_warehouse_master" scripts/` returns exactly two hits, both `scripts/linux/reset_all.sh:205-206`
("7/7 Seeding warehouse bays for every warehouse the orders name"). `scripts/reset_all.ps1` runs
`preflight_ports.py`, `reset_transactional_state.py`, `load_reference_dataset.py`,
`build_knowledge_graph.py` and `verify_graph_ready.py` and stops — no bay seeding. §L's DEF-12 entry
is correct, and it matters on this host specifically (memory `stack-restart-and-reset-all`: the
Windows launcher family is the one actually used here). Correctly left unfixed: launcher scripts are
outside Owns and the lease's own rules forbid running them.

## Judgement

This is the strongest lease in the track on evidence discipline: every number it publishes, I
re-ran and got the same number or a better one — 42 failing ids identical to the 42 registered
(diffed, not asserted), both `assert_known_failures` invocations green, 1093/1093 vitest, drift
clean, 141/141 in the route sweep with the flake it warned about not even recurring, and two live
specs publishing and reverting against the real backend with head 189 → 193 and every value back
where it started. The `Toggle` fix is the highlight — a real interaction defect that only a real
browser could surface, diagnosed to the actual cause (`sr-only`'s clip box pulled outside the
switch by its negative margin) rather than patched around, fixed so the element is hit-testable
where it is painted, and with a test whose comment admits what jsdom cannot prove. The two gaps it
carries — the AI Control Center live loop and CFG-6 A6's startup half — are argued on risk and
testability, with call sites named, not waved away. What holds the verdict is the opposite of that
discipline in two places: three fixes the brief names by ID (CFG-8 A2, CFG-5 H1, CFG-5 H2) vanished
without a line in the ledger while `drop.json` calls item 2 DONE, and A2 is still measurably
2.046:1 on a file this lease edited twice; and the definition-of-done verdict — the artifact the
whole track closes on — rests on a claim about `backend/config/seed/` that is false in a way that
would invite deleting a file the app cannot start without. Both fixes are small and neither
requires new code: say what was left, and say it accurately.
