# RV — CFG-7 @ b8ba94b6 — VERDICT: PASS

Round 2. Base `261dc3bc`; round-1 head `b079a987` (CHANGES_REQUIRED); fixes `ae10f768`; head
`b8ba94b6`. Ledger `CFG-7 step:14`. Definition-of-done item 2 judged against the **amended**
`CFG.brief.md` §6 (trunk `342e5e78`, the wording this review proposed in round 1).
`python -c "import return_platform; print(return_platform.__file__)"` →
`…\.claude\worktrees\cfg-7\backend\src\return_platform\__init__.py`.

Both blocking findings are properly closed — not papered over. F1's fix was measured, not asserted:
the disabled Evaluate button went from **2.046:1 → 8.432:1** and now differs from its enabled state
in fill *and* border, not only the cursor. F2's correction is accurate against the source I checked
myself. Four new advisories, none blocking; one of them is a real gap in the follow-up list the
lease recorded, and it is worth acting on before the next config lease.

## Round-1 findings — dispositions

| ID | Sev | Disposition | Evidence |
|---|---|---|---|
| **F1** | BLOCKING | **CLOSED — fixed and measured.** CFG-8 A2 (`PolicySection.tsx:786` Evaluate), CFG-5 H1 (`UndecidedKeysPanel.tsx:188` "Take packaged file") and, unprompted but correctly, the shared `PublishBar.tsx:56,74` Validate/Publish — the one footer every typed screen inherits, with Publish disabled by default at `dirtyCount === 0` on first paint — all now dim through `disabled:border-outline-variant disabled:bg-surface-container-low` with no opacity utility, so the label keeps full contrast in both states. H2 widened from the one spelling to the property family (`not /opacity-\d/` **and** a real `disabled:(bg\|border)-*` present, plus `not /disabled:text-/` where H1's own counter-example applied) at all three test files. Measurements below. | `PolicySection.tsx:786`, `UndecidedKeysPanel.tsx:188`, `PublishBar.tsx:56,74`; `OverviewSection.test.tsx:281-282`, `PolicySection.test.tsx:263-283`, `PublishBar.test.tsx:58-76`; ledger step:14 |
| **F2** | BLOCKING | **CLOSED — corrected, and the correction is true.** Ledger step:14 and `drop.json.scope_status.8_definition_of_done` now state that `backend/config/seed/e2e_seed_manifest.json` **is** runtime-loaded at import and must not be moved, and confine the not-met verdict to `seed/generation.yaml` and `data_platform/*.yaml`. I re-verified the chain: `operations/seed_manifest.py:35,57` → imported by `api/order_lines.py:95`, `operations/repository.py:66`, `workflows/case_customer_identity.py:54` → `main.py:51`. Correctly appended rather than edited in place, and **nothing was deleted or moved**. | ledger step:14; `drop.json` `8_definition_of_done`, `remaining[3]` |
| A1 | ADVISORY | **CLOSED.** `AiControlCenterPage.tsx:1702` now carries the same `absolute inset-0 z-10 size-full appearance-none opacity-0` as `Toggle.tsx:69`, with a comment naming why it was not the same user-facing defect. No test — the reason given (no harness reaches `ProvidersTab`/`TasksConfigTab`; building one for a geometry fix is disproportionate) is honest and I accept it. The file is outside the literal Owns list -- see the note below the findings table. | `AiControlCenterPage.tsx:1689-1703` |
| A2 | ADVISORY | **CLOSED.** All eight remaining references repointed. `grep -c 'returns/production.yaml\|config/ai_gateway.yaml'` over the file → **0** (the only surviving mention is the explanatory comment at `:267`). | `generate_audit_artifacts.py:106,108,114,120,124,149,151,219` |
| A3 | ADVISORY | **CLOSED.** `fixedTrailing`'s JSDoc now states the precondition and says plainly why it is documented rather than enforced (a generic component would have to know one caller's validation rule). Right call. | `OrderedList.tsx:36-47` |
| A4 | ADVISORY | **CLOSED as far as it can be.** `head_sha` moved `d2eddd76` → `ae10f768`, the last code commit; the head is `b8ba94b6`, the commit that records the hash. A file cannot contain its own commit id, so pointing at the last code commit is the correct resolution, not a residual defect. | `drop.json:5` |
| A5 | ADVISORY | **CLOSED.** Frontend floor `1092 → 1095`, matching the head exactly (`1095` cases / `90` files), and the comment says *why* it moved rather than silently changing the number. | `suite_size_floor.json:133-138,156` |
| A6 | ADVISORY | **CLOSED — noted, no action, correctly.** | ledger step:14 |
| A7 | ADVISORY | **CLOSED.** §L "Net" now reads 14 fixed-and-holding + 1 superseded (06), still totalling 20. | `FINAL_REPORT.md` §L |

## New findings (round 2)

| ID | Sev | Where | What | Why it matters | Fix |
|---|---|---|---|---|---|
| N1 | ADVISORY | `frontend/src/domains/config/ConfigurationPage.tsx:391`; `drop.json.remaining[3]`; ledger step:14 | The recorded follow-up list of residual `disabled:opacity-40` instances names four files. **Answering the question put to me: every one of the four is a config screen or a config-screen primitive** — `DataSourcesSection.tsx` is `/config/source-bindings`, `DocumentEditor.tsx` is the Advanced/JSON mode every typed screen falls back to, `SupportTemplateSection.tsx` is `/config/support`'s Template tab, `KeyValueTable.tsx` is a shared form primitive. **And the list is incomplete**: `ConfigurationPage.tsx:391` — the release **promote** buttons (`RELEASED`, …) on `/config/releases`, disabled whenever `headRevision` is blank, which is their state on first paint — is absent from it, and I measured it at **1.543:1**, the worst disabled control in the whole config domain. `OrderedList.tsx:111,120` and `KeyValueTable.tsx:183,192` (`opacity-30`, icon-only) are also absent. | Not blocking: round 1's condition was "fix **or** record", and the four brief-item-2 IDs are fixed while the rest are now recorded — that condition is met. But a follow-up list that omits the worst instance, on the highest-stakes control in the domain, will send the next lease to the wrong files first. | Add `ConfigurationPage.tsx:391` to `drop.json.remaining` with its measurement, and the two icon-only `opacity-30` sites as a separate, lower-severity class (a glyph, not text — 1.4.11 exempts disabled non-text). |
| N2 | ADVISORY | `frontend/tests/canonical-routes.spec.ts:124` (outside Owns) | The 320px reflow assertion failed in my full sweep (`/config/workflow +23px`) and in **two** further isolated 4-run batches — `/config/agents +544px`, then `/config/return-policy +284px`. **Three firings, three different routes, three different magnitudes, 1-in-4 each time.** That rules out round 2's own change (a 2px `border border-transparent` on `PublishBar`'s Publish) as the cause: a 2px border does not produce +544px, and it would not move between routes. It is the CFG-4 F10 / CFG-5 H5 flake at exactly H5's own documented cadence ("once, at 320, in 4 isolated runs") and, on the third firing, H5's own documented route. | Not a regression and not CFG-7's to fix. But `drop.json` records "141 passed both this lease's own run and RV round 1's independent re-run" as if 141/141 were a property; it is a lucky run. The **axe half is the stable part** — 44/44 in every run I have done across both rounds, including the run whose reflow assertion failed — so the brief's actual acceptance criterion ("zero violations across the swept routes") holds unconditionally. | Say "44/44 axe, every run; the 320 reflow group is the known F10/H5 flake at ~1-in-4, three routes observed" rather than "141 passed". |
| N3 | ADVISORY | `evidence/config_audit/FINAL_REPORT.md` §L, CFG-DEF-12 row | The row credits `18f55e09` as where `reset_all.ps1` was fixed. `18f55e09` only repairs a corrupted path separator (`scriptsackfill_warehouse_master.py` → `scripts\backfill_warehouse_master.py`); the commit that actually added Step 6/6 to the Windows script is **`a90a7a57`** ("reset_all.ps1 seeds warehouse bays after the graph build, as reset_all.sh does (CFG-DEF-12 on the Windows path)"). The row's *claim* is true — I confirmed Step 6/6 present and both scripts chained at `git show 18f55e09:scripts/reset_all.ps1:303-309` — only the attribution is one commit off. | §L is the audit's merge-record pointer; DEF-12 is the one defect this lease itself surfaced. Citing the follow-up repair instead of the fix makes the trail harder to walk. | "fixed on trunk at `a90a7a57`, path separator repaired at `18f55e09`". |
| N4 | ADVISORY | `backend/config/README.md:114-116` (outside Owns) | Under the **amended** §6 item 2 — "each named in `backend/config/README.md` **with its loader**" — two entries are named without one: `data_platform/` ("configuration for the data platform surfaces that have not yet migrated…", loader unnamed; it is `data_platform/graph/sandbox_runner.py:68`'s `--config-dir` default plus `backend/tests/**`), and `seed/` ("fixtures for local/dev seeding, not production runtime configuration") — which is the exact sentence that misled this lease in round 1, and is now known to be wrong for `e2e_seed_manifest.json`. | The amendment landed on trunk (11:11) **after** the fix commits, so CFG-7 could not have satisfied it, and `backend/config/**` is outside its Owns either way. Recorded so the item closes on a real edit rather than on the amendment alone. | Two bullets in `backend/config/README.md`: name `operations/seed_manifest.py` (import-time) and `backend/scripts/generate_seed_data.py:128` under `seed/`, and `sandbox_runner.py:68` + `backend/tests/**` under `data_platform/`. |

Also noted, no action: `AiControlCenterPage.tsx` is outside the literal Owns list, taken on this
review's own A1 — the same shape as round-1 A6, and the diff is exactly the named two-line change.

## Pasted outputs (all re-run by RV on `b8ba94b6`)

Disabled-vs-enabled, computed from the **rendered** tokens in the app's own stylesheet (WCAG
relative-luminance; `page = rgb(247,250,248)`). `p1/p2` are the outlined shape (Evaluate, Take
packaged file, Validate), `p3/p4` the filled shape (Publish):

```
                                  text contrast     fill              border
p1 outlined, ENABLED .........  9.338 : 1        rgb(255,255,255)  rgb(130,141,138)
p2 outlined, DISABLED ........  8.432 : 1  ✓     rgb(241,244,242)  rgb(190,201,198)
p3 Publish,  ENABLED .........  9.630 : 1        rgb(0,78,71)      transparent
p4 Publish,  DISABLED ........  8.432 : 1  ✓     rgb(241,244,242)  rgb(190,201,198)

visibly distinct from enabled?   fill 1.107:1, border 2.018:1   (outlined shape)
                                 fill 8.695:1                   (Publish: dark green -> light grey)

BEFORE (round 1, b079a987): disabled Evaluate = 2.046 : 1, fill and border IDENTICAL to enabled
```

Both disabled states clear 4.5:1 with room to spare (8.432:1), and both differ from their enabled
state in two non-text channels. On the outlined shape the fill delta alone is modest (1.107:1) and
the border is what carries it (2.018:1); on Publish the change is unmistakable. H1's actual
complaint — "the only sighted signal left is a cursor" — no longer holds.

Residual instances, same method: `ConfigurationPage.tsx:391` (promote buttons, `/config/releases`)
**1.543:1**; `DataSourcesSection.tsx:273` (Rebind) **1.409:1**; the
`DocumentEditor`/`SupportTemplateSection`/`KeyValueTable` outlined shape **2.032:1** — the same
value F1 just removed from Evaluate. → N1.

```
$ npx vitest run
 Test Files  90 passed (90)
      Tests  1095 passed (1095)
$ npm run typecheck  -> exit 0      $ npm run lint  -> exit 0

$ python scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend.xml
suite size held: 90 test files/modules, 1095 test cases (floor 90 / 1095)
1084 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed                            [[ exit 0 ]]

$ python scripts/ci/assert_known_failures.py --suite backend --report junit-backend.xml
   (report from RV round 1's own full pytest run at b079a987 -- `git diff --stat
    b079a987..HEAD -- backend/` is EMPTY, so the backend suite is byte-identical)
suite size held: 455 test files/modules, 5446 test cases (floor 455 / 5446)
5446 tests ran, 42 failed, 42 allowlisted
only the 42 known, still-failing tests failed                           [[ exit 0 ]]
   (round 1: those 42 ids diffed byte-for-byte against the registry -- IDENTICAL, 42/42)

$ python scripts/ci/test_assert_known_failures.py
all negative controls passed                                            [[ exit 0 ]]

$ python scripts/check_openapi_drift.py
"commit": "b8ba94b6...", "diffs": [], "status": "PASS", "exit_code": 0
```

Mock sweep, disposable `vite --mode mock --port 5178 --strictPort`, `main.tsx` at
`onUnhandledRequest: "error"`, `--workers=1`:

```
$ E2E_BASE_URL=http://localhost:5178 npx playwright test --project=mock-chromium \
    tests/canonical-routes.spec.ts --workers=1 --reporter=list
  accessibility ......................... 44 of 44 ok, ZERO critical or serious violations
     including /config/overview, /config/policy, /config/deployment,
               /ai/configuration, /ai/providers-models
  reflows 390 / 640-zoom200 / 768 / 1280 / 1440 .......... ok
  x  reflows 320 .......... "/config/workflow (+23px)"
1 failed, 140 passed (5.4m)

$ ... -g "no route scrolls sideways at 320" --repeat-each=4     (batch 1)
  1 failed ("/config/agents (+544px)"), 3 passed (1.6m)
$ ... -g "no route scrolls sideways at 320" --repeat-each=4     (batch 2)
  1 failed ("/config/return-policy (+284px)"), 3 passed (1.6m)
```

Three firings, three different routes, 1-in-4 each — the F10/H5 flake, not round 2's 2px border. → N2.

Live acceptance, disposable `vite --port 5179` (`FRONTEND_BACKEND_TARGET=http://localhost:8000`),
`--workers=1`; `:5173`/`:8000` never touched, both disposable servers stopped and confirmed down
(`5178=000 5179=000`), live stack still up (`8000=200 5173=200`):

```
$ E2E_REAL_BASE_URL=http://localhost:5179 npx playwright test --project=cfg4-e2e \
    e2e/config-policy.spec.ts --workers=1 --reporter=list
  ok 1 [cfg4-e2e] config-policy.spec.ts  widens the return window, previews, then reverts (10.1s)
  1 passed (12.6s)

GET /api/config/runtime        BEFORE (head 193)      AFTER (head 195)
  policy_evaluation.enabled      True                   True
  …purchase_window.days          30                     30
```

Two publishes, 193 → 195, value back where it started.

## Definition of done (`CFG.brief.md` §6, as amended at `342e5e78`)

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Every §G defect fixed or deferred with the ID in the ledger | **Met** | `FINAL_REPORT.md` §L, 20/20 with lease + pointer; count corrected per A7; DEF-12 now closed on both paths (attribution → N3) |
| 2 | `backend/config/` holds only files the runtime **or its own tooling** loads, each named in `backend/config/README.md` **with its loader**; runtime configuration split by function; READMEs match | **Not met — narrowly, and outside this lease's reach** | Clause 1 now holds for every entry except `manifest.yaml`'s inert entry and `dynamic_knowledge/active-schema.example.yaml`, both documented as such. Clause 2 fails for exactly two bullets: `seed/` and `data_platform/` are named without their loaders (N4). The amendment landed after `ae10f768`, and `backend/config/**` is outside Owns — CFG-7 could not have closed it. Two bullets away. |
| 3 | Every section/unit has a typed screen, each proven once live | **Mostly met** | 12 specs in one pass; `config-policy` re-run by me at this head; `AI_GATEWAY`'s two screens remain the one recorded gap, and A1's fix means the next person writing those specs no longer hits the `Switch` interception first |
| 4 | No business value read from env at request time | **Met** (inherited, spot-verified) | CFG-6's hot-adopt mechanism; no CFG-7 change touches it |
| 5 | Suites green, drift green, axe clean | **Met** | `1095/1095` vitest, typecheck, lint, both `assert_known_failures` exit 0, drift `diffs: []`, axe **44/44 zero violations**; the 320 reflow flake is a separate, pre-existing, out-of-Owns assertion (N2) |

## Judgement

The fix round did the thing that was actually missing in round 1: it stopped asserting and started
measuring. F1 was not closed by changing a class string and calling it done — it was closed by
moving the dimming onto a channel that does not touch the foreground, extending the fix unprompted
to `PublishBar`, which is the one footer every typed screen inherits and whose Publish button is
disabled on first paint, and then widening the regression tests from the single spelling that
caused the bug to the property family, with the `PolicySection` test actually *driving* the pending
state rather than reading a className off a static render. My own re-measurement confirms it:
2.046:1 → 8.432:1, and a disabled control that now differs from its enabled twin in fill and border
rather than in cursor alone. F2 is the better half of the round — a factually wrong line in the
lease's own definition-of-done record, corrected by appending rather than by quietly editing, with
the import chain re-derived from the source instead of taken on this review's word, and with
nothing deleted on the strength of it. The residual `disabled:opacity-40` instances are recorded
this time instead of dropped, which is exactly what round 1 asked for; that the list misses the
worst one (`/config/releases`' promote buttons at 1.543:1) is worth fixing, but it is a gap in a
follow-up note, not in the work. The 320 reflow failure I hit is the one thing that could have
looked like a regression, and three firings on three different routes at three different magnitudes
say clearly that it is not — it is F10/H5 at its own documented rate, on its own documented route,
outside Owns. Item 2 remains unmet against wording that changed after the fixes landed, for a
two-bullet README edit in a directory this lease was never allowed to touch. Nothing here blocks
the merge. **PASS.**
