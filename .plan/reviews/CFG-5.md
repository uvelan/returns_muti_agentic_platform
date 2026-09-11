# RV — CFG-5 @ b3d8f06e — VERDICT: PASS

Round 2. Round 1 reviewed `9316eb56` and returned CHANGES_REQUIRED (F1 blocking; F2–F8 advisory).
Fixes are `a53cd675` (substantive) and `b3d8f06e` (drop bookkeeping only); dispositions are in the
ledger under `CFG-5 step:12`. Read-only review: nothing in the worktree was edited except this file,
no `git stash`, `git status --porcelain` clean otherwise. Live evidence came from two disposable
servers started from this worktree (`vite --mode mock --port 5193` for the axe and DOM probes,
`vite --port 5194` for the real-stack e2e), both stopped afterwards and confirmed down; `:5173`/
`:8000` were never restarted and both answer 200 at the end.

Round-2 scope is 9 paths, +388/−39 — six frontend files, one e2e spec, the ledger and `drop.json`.
**The blocker is fixed at the cause and I re-measured it in the browser rather than taking the
className on trust; all seven advisories are taken, most of them more thoroughly than asked.** The
`"warn"` switch in particular pays off immediately and visibly. PASS.

## Round-1 findings — dispositions

| ID | Round 1 | Disposition | RV round-2 check |
|---|---|---|---|
| **F1** | **BLOCKING** — `/config/overview` axe `color-contrast` was a deterministic defect (disabled `Take packaged file` button, `disabled:opacity-40` on `text-on-surface-variant` → 2.04:1), not CFG-4's F10 flake; reproduced 4/4 including 3/3 isolated on a warm server | **Fixed at the cause, and the misdiagnosis retracted in the record.** `UndecidedKeysPanel.tsx:175` drops the opacity utility entirely and adds `disabled:hover:border-outline-control disabled:hover:text-on-surface-variant` so a disabled button cannot pick up the hover treatment. Ledger step:12 opens by superseding step:09's F10 claim; `drop.json`'s `acceptance` and `done` both carry the correction in plain words. | **Verified in the browser, not from the class list.** Computed style of the button forced through its `:disabled` branch: `color rgb(62,73,71)`, `background rgb(255,255,255)`, `opacity 1`, `cursor not-allowed`, **contrast 9.338:1** at 12px — against 2.048:1 before (paste below). Full `dev:mock` sweep `--workers=1`: **135 passed, 0 failed**, where round 1 got 134/1. The axe probe at the spec's own 250 ms settle is clean 3/3, and clean again with the button forced disabled. Regression test at `OverviewSection.test.tsx:245-273`. |
| F2 | ADVISORY — `"bypass"` was chosen on the mistaken belief that MSW's default is silent; the default is `"warn"`, which the sweep does not fail on | **Taken, and the wrong claim corrected in the comment itself.** `main.tsx:82-97` now names the real default, cites the file I cited, and explains why `"warn"` answers the objection `"error"` could not. | **Verified, and it earns its keep on the first run.** The sweep's own console now carries `[MSW] Warning: intercepted a request without a matching request handler` for `GET /api/config/adoption`, `/api/shipment-status-catalog`, `/api/shipments` — three gaps nobody had named, on top of the two `/support` ones the ledger already knew about — while **failing no route** (135/135). That is exactly the trade F2 was about, demonstrated rather than argued. |
| F3 | ADVISORY — the `AI may fabricate success` `Toggle` had one reachable transition and it always 422s | **Taken.** Replaced with a local non-interactive `AiMayFabricateSuccessStatus` (`IntegrationsSection.tsx:178-208`) stating the release's actual value plus the refusal reason; `Toggle` itself untouched, correctly, since `components/forms/**` is outside Owns. | **Verified live on `/config/integrations`:** four read-only readouts render, **zero** interactive controls match `fabricate`, and exactly four `<input type=checkbox>` remain — the four `Enabled` toggles. Reading the value rather than assuming `false` is the right call: a release cut before the validator could carry `true`. |
| F4 | ADVISORY — the `/sync` move silently dropped ~15 design-rationale comments | **Taken in full.** Seventeen blocks restored, logic untouched. | Verified by phrase against `git show b03cbb59:…SyncControlPage.tsx`: 14 of the 15 I named are back verbatim or near-verbatim — the fifteen-hour STALLED run, "three states, not two", the `nodeWrites` pair, the collapsed-form rationale, the incremental default, the surviving error paragraph, the `NaN` omission, the scopes match, the no-cursor skip, the targeted-run note, "two mechanisms, one history". See H4 for the one still missing. |
| F5 | ADVISORY — four paths outside Owns not flagged as an excursion | **Taken.** A paragraph added to step:07 naming `SyncRedirect.tsx`, both deleted `SyncControlPage` files and `domainScreens.ts`, and why each is forced by item 7. | Verified; the reasoning is the one I would have given. |
| F6 | ADVISORY — `registry.ts:182` pointed at the deleted `BusinessSection.tsx` | **Taken.** Repointed at `ConfigurationPage.tsx`'s module docstring, which carries that history now. | Verified. The other mentions are historical statements, not pointers, and are rightly left. |
| F7 | ADVISORY — stale `head_sha`; "13 routes" where there are 10 | **Taken.** `drop.json:5` is now `a53cd675…`; step:01 reads "**10** total — agents 3, schema-releases 5, replay/compare 2". | Verified. `head_sha` names the substantive commit and `b3d8f06e` changes only that one line, which is this lease's own established pattern. See H3 for the one bookkeeping line that did not follow. |
| F8 | ADVISORY — `.nth(1)` picked the topic by position | **Taken.** `config-integrations.spec.ts:52-53` scopes to `div.rounded-lg` with `hasText: "External support mirror"` first, and the docstring that used to justify the positional form now explains the scoping. | **Verified against the live stack, not just by reading**: the spec still passes end to end and still flips the right topic — `external_support_mirror` is the field that moved and came back (paste below). |

## New — round 2

| ID | Sev | file:line | What | Why | Fix |
|---|---|---|---|---|---|
| H1 | ADVISORY | `UndecidedKeysPanel.tsx:175` | With `opacity-40` gone, the disabled button is now **pixel-identical to the enabled one** except for the cursor: measured `color`, `background`, `border-color` and `opacity` are the same in both states (paste below). | No WCAG rule is broken — disabled controls are exempt from 1.4.3, the `disabled` attribute is still set, and a keyboard or screen-reader user is told. But the only sighted signal left is a cursor, which a touch user never sees and a glance never catches. The old treatment was wrong about *how* it dimmed, not about dimming. | A non-text channel: `disabled:border-outline-variant` (or `disabled:bg-surface-container-low`) restores the visual difference without touching the foreground the floor is computed against. |
| H2 | ADVISORY | `OverviewSection.test.tsx:271` | The regression test asserts `button.className` matches no `opacity-\d` utility. | It pins the exact spelling that caused this defect, not the property it broke. `disabled:text-outline` — the token that caused the *other* contrast bug this same lease fixed, in `AgentsSection.tsx` — would sail through it. The test's own comment is honest that a className check is a proxy; the proxy could be one notch wider for nothing. | Also assert the class list still carries `text-on-surface-variant` and no `disabled:text-*` override, which catches the family rather than the instance. |
| H3 | ADVISORY | `drop.json:6`; ledger step:12 closing paragraph | Step:12 says "`drop.json`: … `status` moved from PENDING to RV-ready". It is still `"status": "PENDING"`. | The `acceptance`/`done` corrections F7 and F1 asked for *are* all there, so this is the one line of the described edit that did not land — and status is what a merge gate reads. | Set it, or drop the sentence. |
| H4 | ADVISORY | `DataSourcesSection.tsx:400` | One rationale is still absent: the old `RailFact label="Records"` carried "FULL and INCREMENTAL both complete green, so a run that is quietly rescanning production every tick is invisible without this line". Its successor `SummaryFact label="Records"` carries no reason. | Fourteen of fifteen restored is a good answer to F4; this is the fifteenth, and it is the one that explains why the field is on the summary at all. | One line above it. |
| H5 | ADVISORY | `tests/canonical-routes.spec.ts:124` (outside Owns) | The reflow flake step:12 reports at `/config/return-policy (+214px)` @390 is real and I saw it too — once, at **320**, in 4 isolated runs of the reflow group, with both full sweeps passing. Genuinely intermittent, and I could not reproduce it. | The classification is right and the route is outside Owns — but the test already re-measures 500 ms later and only reports overflow that *persists*, so whatever this is, it is not a single mid-layout sample. Calling it "the F10 signature" is fair as a match to the pattern; treating it as understood is not. | Carry it as an open item against the sweep's owner with this note, rather than folding it into F10. |

## Commands

```
$ npx vitest run                                   # whole frontend suite, from frontend/
 Test Files  87 passed (87)
      Tests  1058 passed (1058)                     # 1057 + the F1 regression test
   Duration  67.56s                                 [exit 0]

$ npm run typecheck      # tsc -b --pretty false    typecheck exit: 0
$ npm run lint           # eslint . --max-warnings=0     lint exit: 0

$ npx playwright test --project=mock-chromium tests/canonical-routes.spec.ts --workers=1 --reporter=list
identity pending: 0 of 42
heading mismatch: 0
  135 passed (5.0m)                                 # round 1: 134 passed, 1 failed

$ git diff --stat b03cbb59..HEAD -- backend/                      (empty)
$ git diff --stat b03cbb59..HEAD -- frontend/openapi/ *openapi*   (empty)
```

## F1 — the fix, measured in the browser

Both states read off the live page at `:5193`; the disabled row is the same button with its
`:disabled` branch forced, so the numbers are what CSS actually resolves, not what the class list
implies:

```
state                        color             background         opacity  cursor        contrast
enabled (as rendered)        rgb(62, 73, 71)   rgb(255,255,255)   1        pointer       9.338 : 1
disabled (forced, CSS only)  rgb(62, 73, 71)   rgb(255,255,255)   1        not-allowed   9.338 : 1

round 1, disabled:           rgb(178,182,181)  rgb(255,255,255)   0.4      not-allowed   2.048 : 1
```

12px normal text, so 4.5:1 is the floor; 9.338:1 clears it with room. The token arithmetic agrees —
`on-surface-variant` #3e4947 on `surface-container-lowest` #ffffff is 9.338:1, and #b2b6b5 is exactly
that colour composited at 40% over white, which is what `opacity-40` was doing.

Axe, run directly against the warm mock server at the sweep's own 250 ms settle, and again with the
button forced into its disabled state:

```
=== 250ms run 1 ===   NO BLOCKING VIOLATIONS
=== 250ms run 2 ===   NO BLOCKING VIOLATIONS
=== 250ms run 3 ===   NO BLOCKING VIOLATIONS
=== button forced disabled ===
NO BLOCKING VIOLATIONS (axe run with the button forced disabled)
```

Round 1's probe returned `color-contrast serious` on this exact node 3/3 under the same conditions.

## F2 — `"warn"` working, in the sweep's own output

```
[WebServer] [vite] (client) [console.warn] [MSW] Warning: intercepted a request without a
[WebServer]   matching request handler:  • GET /api/config/adoption
[WebServer]   ...                        • GET /api/shipment-status-catalog
[WebServer]   ...                        • GET /api/shipments
  ok 129 /operations has no critical or serious violation (1.8s)
  ok 132 /shipments has no critical or serious violation (1.6s)
  135 passed (5.0m)
```

Three unhandled requests nobody had named, surfaced on the first run after the switch, with no route
failing — `console.warn` is not what `canonical-routes.spec.ts:56-58` fails on. Under `"bypass"` these
were unreportable by construction; under `"error"` they would have failed four routes this lease does
not own. `"warn"` is the option that was always right.

## F3 and F8 — live checks

`/config/integrations` at `:5193`:

```
{ "readoutCount": 4,
  "readoutText": "AI may fabricate success | Off -- read-only | Whether an AI-authored message may
                  claim this integration succeeded before it is confirmed. The release validator
                  refuses true on any of the four topics unconditionally (not only in production),
                  so there is no reachable ...",
  "interactiveControlsNamedFabricate": 0,
  "enabledToggles": 4 }
```

Four readouts, no control to switch, four checkboxes left — the `Enabled` toggles, which are real.

## The live e2e, and the revert

```
$ npx vite --port 5194 --strictPort            # from <worktree>/frontend, stopped afterwards
$ E2E_REAL_BASE_URL=http://localhost:5194 npx playwright test --project=cfg4-e2e \
    e2e/config-overview.spec.ts e2e/config-integrations.spec.ts --workers=1 --reporter=list --timeout=90000
Running 2 tests using 1 worker
  ok 1 [cfg4-e2e] › config-integrations.spec.ts:39:3 › flips external_support_mirror.enabled … reverts (10.3s)
  ok 2 [cfg4-e2e] › config-overview.spec.ts:28:3 › reads the active release and renders the
       undecided-keys panel from the live packaged-drift (2.0s)
  2 passed (14.7s)
```

`GET /api/config/runtime` read by me before and after, independently of the specs:

```
BEFORE  release publish-6f720d6e57204bad  head 133
AFTER   release publish-cbb472ad451d468d  head 135        (one edit, one revert; Overview only reads)
integrations.external_support_mirror  {enabled: False, topic: return-support.ticket.create,
                                       authority: EXTERNAL_TICKET_SYSTEM,
                                       ai_may_fabricate_success: False}     identical
```

The integrations spec was rerun deliberately: F8 changed how it finds its field, and the only way to
know the card-scoped locator still targets `external_support_mirror` is to watch that key move and
come back. It did.

## H5 — the reflow failure, classified

```
$ for i in 1 2 3; do npx playwright test … --workers=1 -g "no route scrolls sideways"; done
  run 1: 6 passed (2.1m)
  run 2: 6 passed (2.1m)
  run 3: 5 passed, 1 failed  -- "no route scrolls sideways at 320"
$ npx playwright test … -g "no route scrolls sideways at 320"        1 passed (24.0s)
$ (both full sweeps this round)                                      135 passed, 0 failed
```

One failure in four isolated runs of the group, at a different width than the implementer saw, passing
on immediate re-run and in both full sweeps. That is intermittent and unreproducible, which is the
opposite of F1's 4-for-4, and it is on a route outside this lease. Classified as the implementer
classified it — with the caveat in H5 that the test's own double-measure means this is not simply a
paint race, so it should be carried rather than closed.

## Judgement

The blocker was fixed where the defect was, and — the part that matters after a round-1 finding about
a wrong diagnosis — the record was corrected rather than quietly updated: step:12 opens by naming
step:09's F10 attribution as wrong, explains why the race made an inconsistent pass/fail record look
like flakiness, and pushes the same correction into `drop.json`'s `acceptance` and `done` fields where
a merge gate would read it. The fix itself resists the obvious wrong move of dimming the text some
other way; it takes the fade out and instead stops the disabled button from picking up hover, which is
the thing the fade was half-doing. I did not accept the class list as proof — I measured both states in
the page, and the disabled branch resolves to the same 9.338:1 the enabled one does. Of the advisories,
F2 is the one that repaid the round: switching to `"warn"` surfaced three unhandled requests nobody had
named, on the first sweep, without failing a single route, which is precisely the argument the original
`"bypass"` comment talked itself out of on a false premise. F3's readout reads the release's own value
instead of assuming the validator has always been there; F4 put back seventeen blocks after diffing
against the original rather than reconstructing from my list; F8 was verified where it counts, against
the live stack, because a locator change to a spec that publishes is only as good as the key it still
moves. What is left is five advisories of my own, four of them a line each and none of them a reason to
hold a merge: a disabled state that is now inert but indistinguishable, a regression test one notch
narrower than the family of defects it guards, a `status` field that did not get the edit its own ledger
entry describes, one rationale comment still missing, and an intermittent reflow failure on someone
else's route that deserves to be carried rather than filed under a label. PASS.
