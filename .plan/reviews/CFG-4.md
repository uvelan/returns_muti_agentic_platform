# RV — CFG-4 @ a4d623a5 — VERDICT: CHANGES_REQUIRED

Round 1. Worktree `.claude/worktrees/cfg-4`, branch `feat/cfg-4-screens-a`, head `a4d623a5` (code at
`08c9001d`, bookkeeping at `a4d623a5`), base `656ba175`. Read-only on code: nothing in the worktree
was edited, no `git stash`, `git status --porcelain` shows only this file. Behavioural evidence was
taken from two disposable servers started from this worktree (`vite --port 5183` proxying to the
shared backend at `:8000`, and `dev:mock --port 5185`), both stopped afterwards; `:5173`/`:8000` were
never restarted, and every live value the e2e run changed is byte-identical to its pre-run value
(pasted below).

Nine commits, 41 files, no backend change (`git diff --name-only 656ba175..HEAD | grep -v '^frontend/'`
returns `scripts/ci/known_test_failures.json` only, which the brief's acceptance names). The three
files outside the Owns list — `frontend/src/api/principal.ts`, `frontend/playwright.config.ts`,
`frontend/tsconfig.e2e.json` — are each additive, necessary, and recorded with their reasoning in the
ledger (step:02, step:08). Scope is clean.

Most of this lease is right, and right for stated reasons: every hand-written option list I checked
resolves to a real model enum, the registry test was fixed rather than loosened, C1/A5/A6/A7 landed
with tests, and the live e2e publishes and reverts for real. Two findings block.

## Findings

| ID | Sev | file:line | What | Why | Fix |
|---|---|---|---|---|---|
| F1 | **BLOCKING** | `frontend/src/domains/config/UndecidedKeysPanel.tsx:79-88` (the sentence at `:87`) | `diffSummary` infers "there is no active release" from `would_adopt` not containing the unit. But `_would_adopt` (`packaged_adoption.py:552-584`) reports a key only when `merged[key] == packaged[key]`, and `_carry_forward` (`:228-243`) gives an **undecided** key `merged[key] = _fill_absent_leaves(value, active[key])` — the release's own value, kept. So an undecided key with an active release is, by construction, absent from `would_adopt`, and the panel tells the operator *"No active release to compare against yet -- adopting is refused until one exists."* | It is false on exactly the rows the panel exists for, and it contradicts both the "Active release" card three inches above it and the enabled "Take packaged file" button beside it, which would publish a release immediately. On the live stack it is 6 rows out of 6 (paste below); in `dev:mock` it is 1 of 1. The brief's own required summary "from `would_adopt`/`filled_leaves`" is therefore dead code for real rows — `filled_leaves` is computed *only* for undecided keys (`packaged_adoption.py:623-629`) and can only ever render in the branch undecided keys never reach. No test asserts the summary text, and the Overview e2e is read-only, so nothing caught it. | Decide the "no active release" case from what the panel already knows (a null `headRevision`, or the runtime's `release_id`), not from `would_adopt`. For an undecided key with an active release, say what carry-forward actually did: the release's value is kept, N leaves filled, and taking the file replaces it. Assert the sentence in `OverviewSection.test.tsx` for the `undecided:["discovery"], would_adopt:[]` fixture that is already there. |
| F2 | **BLOCKING** | `frontend/src/domains/config/TypedSectionScreen.tsx:36,49` vs `:153` and `:181` | `canWrite` is consumed in exactly one place — the `advanced` branch's `DocumentEditor` (`:153`, which does gate properly at `DocumentEditor.tsx:406`). The typed branch renders `renderTyped(...)` (`:181`) with no `fieldset disabled`, and `grep -n "disabled\|readOnly\|fieldset" DiscoverySection.tsx ReturnPolicySection.tsx FulfilmentSection.tsx` returns only `policy_evaluation.disabled_reason` — no control on any of the three screens is gated. A principal without `config.release.write` can edit every typed field. | The brief's design paragraph is explicit: "capability `config.release.write` gates editing, `config.release.promote` gates Publish". Only the second half is implemented. The `.write`-less case is untested: all three screen tests cover only the missing-`promote` case (`DiscoverySection.test.tsx:164-169` and peers). `principal.ts`'s own new comment names the role shape this matters for — a principal with `.promote` but not `.write` gets an enabled Publish on an editable form and a 403 from the backend. | Wrap the typed branch the way `DocumentEditor.tsx:406` already does (`<fieldset disabled={!canWrite}>` plus the same read-only notice), and add the read-only test per screen the acceptance implies. |
| F3 | ADVISORY | `frontend/src/domains/config/FulfilmentSection.tsx:312`; `runtimeSlice.ts:30-32` | The one indexed error lookup in the lease builds `shipment_tracking.statuses.${index}`; the backend emits `shipment_tracking.statuses[0].code` (`releases.py:394-412`, `_dotted_error_path`, "`agents[2].version`, not `agents.2.version`"). `errorsByPath` does no normalisation, so a status-rung error never reaches its field. | Not lossy — the error still shows in the page-level `ValidationErrors` — but it is a silent no-op on the one path where the convention actually differs, and CFG-3b's A1 already solved this: `DocumentEditor.tsx:61-63` `normalizeErrorPath` is two lines away. | Normalise in `errorsByPath` (reuse/export `normalizeErrorPath`) and build the path as `statuses[${index}]`; one test with a bracketed path. |
| F4 | ADVISORY | `CFG.ledger.md:2103-2110`, `configuration.ts:136-139`, `UndecidedKeysPanel.tsx:11-17` | CFG-3a's F5 is *"rollback narrower than 'any refusal'"* (`.plan/reviews/CFG-3a.md:19`), a backend `except` clause on `adopt_packaged`, explicitly carried to CFG-4. The lease redefines "F5" as the unit-naming vocabulary and records it as addressed. | The vocabulary note is genuinely good work; the misattribution means the real F5 now reads as closed while nothing about it changed, and CFG-4 could not have closed it anyway (brief: "Must not touch: backend"). | Re-label the note (it needs no ID), and carry the real F5 to whichever lease may touch `backend/`. |
| F5 | ADVISORY | `frontend/src/domains/config/TypedSectionScreen.tsx:83-95`; `DiscoverySection.tsx:62` | A 409 (the implementer hit a real one, step:08) surfaces its message through `PublishBar`'s `error`, and the draft survives — both good. But `publish` has no `onError`, so `["config"]` is never invalidated and `expectedHeadRevision` stays stale: pressing Publish again reproduces the same conflict forever. The only refresh that fixes it changes `active.releaseId`, which is the editor's React `key`, so it silently discards the draft. | The lock is the feature; recovering from it is the missing half, and the failure mode is "the button no longer works and nothing says why". | On a revision conflict, refetch and tell the operator the head moved, keeping the draft (the patch is computed against `loaded`, so a re-key is what loses it). |
| F6 | ADVISORY | `frontend/src/domains/config/TypedSectionScreen.tsx:76`, `PublishBar.tsx:46-48` | `dirtyCount = Object.keys(patch).length` counts **top-level section keys**, so twelve edited fields inside `discovery` read as "1 change staged". | Publish-gating on it is correct (0 = nothing staged); the number shown to an operator about to publish is not what it claims. | Count changed leaves, or label it "1 section changed". |
| F7 | ADVISORY | `frontend/src/domains/config/FulfilmentSection.tsx:170` | `bay.eligible_statuses` suggestions are `AWAITING_RECEIPT, WAREHOUSE_STAGED, PLANNED, STAGED_AT_BRANCH, LICENSE_PLATE_ASSIGNED, UNKNOWN`. `BayConfiguration.eligible_statuses` is `tuple[NonBlank, ...]` — no enum — and the live release's actual values are `AWAITING_RECEIPT, WAREHOUSE_RECEIVED, INSPECTION_COMPLETE`: two of the three in use are missing, and `INSPECTION_COMPLETE` exists nowhere in `backend/src`. | Free text is accepted so nothing is refused, and the same "best effort" reasoning is *documented* for `PathPicker` (`knownSourcePaths.ts`) — it is not documented here, and a list that omits the values the deployment is running reads as authoritative when it is not. | Either drop the list, seed it from the current value plus the known codes, or carry `knownSourcePaths.ts`'s own caveat into the hint. |
| F8 | ADVISORY | `OrderedList.tsx:21`, `KeyValueTable.tsx:46` | A6's new `error` props are correct and tested, but no screen passes one, so a validation error whose path names a whole list (`discovery.identification_fields`) still only reaches the page summary. | The advisory asked for the prop; the value is in the wiring. | Pass `errorMap.get(<list path>)` at the list call sites. |
| F9 | ADVISORY | `DiscoverySection.test.tsx`, `ReturnPolicySection.test.tsx`, `FulfilmentSection.test.tsx`, `OverviewSection.test.tsx` | No test anywhere covers a **refused** publish. `publish` is mocked resolved in all four; a 409 and a 422 (`releases.py:391` — a plain string `detail`, so per-field mapping is impossible on publish and only `Validate` gives it) are unexercised. | Every behaviour in F5 and the "press Validate for field-level errors" contract is currently unasserted. | One rejected-publish test per screen: error shown, draft intact. |
| F10 | ADVISORY | `frontend/playwright.config.ts` `webServer` (`dev:mock ... --force`) | The `dev:mock` sweep failed for me **2 out of 2** runs that let Playwright boot its own cold mock server (`/config has no critical or serious violation`, `color-contrast`, serious — `/config` being the route CFG-4 changed most), and passed `129 passed` against an already-warm `dev:mock` server, 3/3 in isolation, and under a standalone axe probe at the spec's own 250 ms settle. So the ledger's `129 passed` reproduces; the cause is the cold `--force` dev server still compiling CSS when `settle` gives up after 250 ms. | Pre-existing, not CFG-4's — but CFG-4 made `/config` the heaviest route in the manifest, so this gate is now flaky on a first run. | Not this lease's to fix; worth a note where the release gate runs it (warm the server, or wait for a stylesheet rather than a fixed 250 ms). |

**Carried advisories — verified:** C1 fixed at `DocumentEditor.tsx:833-848` with a real test
(`DocumentEditor.test.tsx:183-221`) and the ledger's reason for a *second* effect rather than the
review's literal one is correct — `pending` in the first effect's deps would re-run the cleanup on
every keystroke. A5 (`KeyValueTable.tsx:211-254`, `aria-describedby` + `role="alert"`), A6
(`error` props, see F8), A7 (`PublishBar.tsx:37-40`, shared id) all landed with tests. A2 is
correctly left as the documented limit CFG-3b's RV already accepted. A8 is discharged by the lease's
own `/validate` usage. CFG-3a F11 is handled honestly — both shapes rendered side by side,
`OverviewSection.test.tsx:97-110` asserts both from one response.

**Contract items verified:** enum option lists all resolve to real model members —
`ELIGIBILITY_DECISIONS` = `EligibilityDecision` (`stage_results.py:58-62`), the window basis =
`ReturnWindowBasis` (`vocabulary.py:150-154`), stock classification = `StockClassificationDefault`
(`:238-261`), `PROJECTION_STATUSES` = the five-value `Literal` (`return_configuration.py:946-948`),
`REQUIREMENT_DIMENSIONS` = exactly the seven `AwaitingDimension` members a table row may name
(`case_projection/vocabulary.py:140-142`), `RETURN_REASON_SUGGESTIONS` = all twelve non-`UNKNOWN`
`ReturnReason` members with the hint naming the enum, and the return-method options come from the
document's own `normalized_return_methods` rather than a literal. The two deviations from the design
table (`selection_vocabulary` as tag lists, no `bay` capacity fields) are both cases where I read the
model and the model agrees with the implementer, not the table. `registry.test.ts` was fixed, not
loosened — both assertions still pin exact sets and a fourth collision still fails —
`known_test_failures.json`'s frontend list is `[]`, `routeManifest.ts` needed no edit (routes derive
from `DOMAINS`), and every one of the ten sections removed from `BusinessSection` has a typed screen
plus an `EDITED_ELSEWHERE` pointer, tested at `BusinessSection.test.tsx:123-148`.

## Commands

```
$ npx vitest run                       # whole frontend suite, from frontend/
 Test Files  83 passed (83)
      Tests  1002 passed (1002)
   Duration  65.52s

$ npm run typecheck                    # tsc -b --pretty false
typecheck exit: 0
$ npm run lint                         # eslint . --max-warnings=0
lint exit: 0
```

## The live e2e run, and the revert

Disposable `vite --port 5183` from this worktree (`.env`'s `FRONTEND_BACKEND_TARGET=http://localhost:8000`),
stopped afterwards.

```
$ E2E_REAL_BASE_URL=http://localhost:5183 npx playwright test --project=cfg4-e2e --workers=1 --reporter=list --timeout=60000
Running 4 tests using 1 worker
  ok 1 [cfg4-e2e] › e2e\config-discovery.spec.ts:31:3 › adds an item condition, publishes, ... then reverts (7.5s)
  ok 2 [cfg4-e2e] › e2e\config-fulfilment.spec.ts:26:3 › adds a bay-eligible status, ... then reverts (7.5s)
  ok 3 [cfg4-e2e] › e2e\config-overview.spec.ts:28:3 › reads the active release and renders the undecided-keys panel (2.6s)
  ok 4 [cfg4-e2e] › e2e\config-return-policy.spec.ts:26:3 › adds a freight keyword, ... then reverts (5.8s)
  4 passed (26.6s)
```

`GET /api/config/runtime` read by me before and after, independently of the specs:

```
head_revision before/after: 99 105           (six publishes: three edits, three reverts)
release       before/after: publish-64402828d3444932 publish-79a0ff332b7d4c2a
selection_vocabulary.conditions               identical: True | E2E residue: []
return_policy...freight_keywords              identical: True | E2E residue: []
bay.eligible_statuses                         identical: True | E2E residue: []
```

The three target fields are legitimate: `selection_vocabulary.conditions` is documented in the model
as deliberately *not* constrained (`return_configuration.py:832-837`), and the `strong_anchors` rule
the implementer hit is real (`return_configuration.py:451`) — retargeting the spec rather than
weakening the screen was the right call. The specs depend on the live `dev-operator` principal
carrying `config.release.promote`; it does (`GET /api/principal` → `console_admin` with
`config.release.{read,write,promote}`), so `config.release.write` is genuinely advertised and nothing
is hardcoded — `can()` reads the principal, and no new file mentions a role name.

## F1, measured on the live stack

```
$ curl -s http://localhost:8000/api/config/packaged-drift
{"RETURN_PLATFORM":{"undecided":["agents","clarification_policy","policy_evaluation",
  "return_eligibility_policy","return_policy","support_ingress"],"would_adopt":[],"filled_leaves":[]},
 "AI_GATEWAY":{...,"would_adopt":[]},"DEPENDENCY_SIMULATION":{...,"would_adopt":[]}}

$ curl -s http://localhost:8000/api/config/runtime | head
{"release_id":"publish-79a0ff332b7d4c2a","head_revision":105, ...}
```

`/config/overview` rendered from that response (text of the live page at `:5183`):

```
ACTIVE RELEASE            publish-79a0ff332b7d4c2a
...
RETURN_PLATFORM: agents
No active release to compare against yet -- adopting is refused until one exists.     [Take packaged file]
RETURN_PLATFORM: clarification_policy
No active release to compare against yet -- adopting is refused until one exists.     [Take packaged file]
RETURN_PLATFORM: policy_evaluation
No active release to compare against yet -- adopting is refused until one exists.     [Take packaged file]
   ... the same sentence on return_eligibility_policy, return_policy, support_ingress ...
WOULD ADOPT ON THE NEXT START
Nothing would be adopted from the packaged file right now.
```

Six rows, six false sentences, six enabled buttons, under a card naming the active release. This also
answers the panel's other open question: `policy_evaluation` and `support_ingress` — operator
decisions — are indistinguishable from a stale key, and the only consequence stated per row is the
wrong one. (The confirm dialog does say "This publishes a new release", which is the honest part.)

## The dev:mock route sweep

```
$ npx playwright test --project=mock-chromium tests/canonical-routes.spec.ts --reporter=list
  1 failed
    [mock-chromium] › tests\canonical-routes.spec.ts:173:5 › accessibility › /config has no critical or serious violation
      - Array []
      + Array [ "color-contrast: Elements must meet minimum color contrast ratio thresholds" ]
  128 passed (2.0m)

$ ... same command, second full run
  1 failed (the same test)   128 passed (2.0m)

$ ... -g "/config has no critical or serious violation" --repeat-each=3
  3 passed (11.4s)

$ node <standalone axe probe, same tags, dark scheme, the spec's own 250ms settle>
done                                            # zero critical/serious violations

$ E2E_BASE_URL=http://localhost:5185 npx playwright test --project=mock-chromium tests/canonical-routes.spec.ts --reporter=list
                                                # against an already-warm dev:mock server
identity pending: 0 of 40
heading mismatch: 0
  129 passed (1.9m)
```

The sweep the ledger claims is real (`129 passed`, all four CFG-4 routes included); the two failures
above are the cold `--force` dev server losing a race with the spec's fixed 250 ms settle — F10.

## Judgement

The engineering underneath this lease is careful in the way that matters: every place the brief's
design table and the actual pydantic model disagreed, the implementer read the model, followed it,
and wrote down why — `selection_vocabulary` is two tuples and not a mapping, `BayConfiguration` has
no capacity fields, five "enum" fields are free strings and stayed text inputs, and the option lists
that *are* hardcoded turn out to be exact transcriptions of `EligibilityDecision`, `ReturnWindowBasis`,
`StockClassificationDefault`, the `projection_status` `Literal`, `AwaitingDimension`'s seven required
members and `ReturnReason`. C1 was fixed at the cause and the ledger's account of why the review's
literal two-line suggestion was wrong is correct and demonstrable. The registry failures were fixed
by deciding the question they encoded rather than by relaxing the assertion, and the frontend known-
failure list is genuinely empty. The live e2e run publishes and reverts for real, and I verified the
revert independently rather than taking the spec's word for it. What blocks is narrower than any of
that and lives in the two places the lease had the least feedback. F1 is a panel that, on every row
the live stack actually has, tells an operator the opposite of what the system is doing — the summary
was derived from `would_adopt`'s complement, which is exactly the inference CFG-3a's F1 was blocking
for, and it survived because the one test fixture that reproduces the state never asserts the
sentence and the Overview e2e deliberately writes nothing. F2 is the other half of a capability
sentence the brief spells out in full: the prop is threaded, typed and passed, and then used only on
the JSON path, so three screens are editable by a principal the backend will refuse. Both are small
changes with obvious tests, and the rest of the round is sound.
