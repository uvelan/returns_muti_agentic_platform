# RV — CFG-4 @ 45c14660 — VERDICT: PASS

Round 2. Round 1 reviewed `a4d623a5` and returned CHANGES_REQUIRED (F1, F2 blocking; F3–F10
advisory). Fixes are `efee678e` (substantive) and `45c14660` (drop bookkeeping only); dispositions are
in the ledger under `CFG-4 step:10`. Read-only review: nothing in the worktree was edited except this
file, no `git stash`, `git status --porcelain` clean otherwise. Live evidence came from a disposable
`vite --port 5187` started from this worktree and stopped afterwards; `:5173`/`:8000` were never
restarted, and the three fields the e2e run touches are byte-identical before and after (pasted).

Round-2 scope is 16 files, +754/−43, all frontend, all inside the Owns list plus the two already-
recorded exceptions. **Both blocking findings are fixed at the cause, six of the eight advisories are
taken, and the two left carry reasons I accept.** PASS.

## Round-1 findings — dispositions

| ID | Round 1 | Disposition | RV round-2 check |
|---|---|---|---|
| **F1** | **BLOCKING** — `diffSummary` inferred "no active release" from absence in `would_adopt`, so every undecided row (6 of 6 live) claimed adopting was refused next to an enabled button that would publish | **Fixed at the cause.** `UndecidedKeysPanel.tsx:106-107` takes `hasActiveRelease` from `headRevision !== null` (`:157`) — the signal that actually answers the question — and an undecided key now states what `_carry_forward` did plus the action's consequence. | **Verified live, against the same six-key drift response.** Page text below: the false sentence is gone from all six rows and each carries "The release's own value is kept for now. Taking the packaged file for this whole key replaces every value the release holds for it." Two new tests (`OverviewSection.test.tsx:159-215`), the first built on the live stack's exact shape, the second on a `filled_leaves`-populated fixture — both would fail against the old code. `filled_leaves` now has a branch that can render it, which it could not before. |
| **F2** | **BLOCKING** — the typed branch ignored `canWrite`; a `promote`-without-`write` principal could edit every field on three screens | **Fixed the same way `DocumentEditor` does.** `TypedSectionScreen.tsx:231-238`: `renderTyped`'s output is wrapped in `<fieldset disabled={!canWrite}>` with the same read-only notice, so the gate cascades natively and no screen needed changing. | **Verified.** One test per screen with `promote` granted and `write` withheld (`DiscoverySection.test.tsx:176`, `ReturnPolicySection.test.tsx:167`, `FulfilmentSection.test.tsx:196`), each asserting a representative control is disabled *and* the notice renders — the case none of the round-1 tests covered. Inert for a writer: live DOM on `/config/discovery` shows exactly one fieldset, `disabled: false`, every sampled control enabled, no notice (paste below), and the e2e still edits and publishes. |
| F3 | ADVISORY — indexed error paths never matched (`statuses.0.code` vs `statuses[0].code`) | **Taken, and de-duplicated.** `normalizeErrorPath` moved to `jsonPath.ts:78-92` (the `react-refresh/only-export-components` reason given is real) and `errorsByPath` normalises through it (`runtimeSlice.ts:48-50`); `DocumentEditor` imports the same function instead of a private copy. | Verified. `FulfilmentSection.test.tsx:225-243` feeds a bracketed path and asserts it lands on status card 0's Code field *and* not on card 1 — not a tautology. Both consumers of the wire convention now agree by construction. |
| F4 | ADVISORY — CFG-3a's "F5" mislabelled | **Taken.** Both comments corrected (`UndecidedKeysPanel.tsx:11-16`, `configuration.ts:138-141`), and the ledger records that the real F5 — the backend `except` breadth on `adopt_packaged` — **stays open** and is carried onward. | Correct, and the right outcome: CFG-4's brief forbids `backend/`, so the finding could only be re-carried, not closed. |
| F5 | ADVISORY — a 409 stranded the operator (stale head forever; the only refresh discarded the draft) | **Taken.** `TypedSectionScreen.tsx:69-82, 112-131`: on a 409 the head is re-read with a direct `configApi.runtime()` call, held in `headRevisionOverride`, and surfaced as a notice — deliberately off the shared query cache so the parent's `key={active.releaseId}` cannot remount the editor. | **Verified, including the remount question.** `DiscoverySection.test.tsx:210-249` publishes into a 409, asserts the notice names head 44, asserts the draft still reads 300000 (no remount), retries and asserts the second call carries 44. The claim also holds structurally: nothing writes the query cache on this path, and `main.tsx:39` sets `refetchOnWindowFocus: false`, so the remaining remount triggers are explicit invalidations only. |
| F6 | ADVISORY — `dirtyCount` counts sections, not leaves | **Left, with a ledger line.** | Accepted. It is a label, it never gates wrongly (0 still means nothing staged), and the fix is a patch walk better done deliberately. |
| F7 | ADVISORY — `bay.eligible_statuses` suggestions missed two of the three values the deployment runs | **Taken.** `FulfilmentSection.tsx:169-191`: suggestions are now the loaded value unioned with the documented codes, and the hint says there is no enum and the list is not exhaustive — the caveat `knownSourcePaths.ts` already carried. | Verified: seeding from `asStringArray(bay.eligible_statuses)` is accurate to whatever is running by construction, which is stronger than patching the literal. |
| F8 | ADVISORY — `error` props existed but nothing passed one | **Taken at nine call sites** across the three screens (`DiscoverySection.tsx:140,189`, `ReturnPolicySection.tsx:146,175`, `FulfilmentSection.tsx:121,135,210,224,291`). | Verified; each path is one the backend's `loc` can actually produce (a list or mapping named whole, not a row). |
| F9 | ADVISORY — no test anywhere covered a refused publish | **Taken.** One 422 test per screen (`DiscoverySection.test.tsx:188` and peers): the message shows and the draft is intact. | Verified. 422 is the right shape to pin — `releases.py:391` sends a plain string `detail`, so page-level is all publish can give, and `Validate` remains the per-field path. |
| F10 | ADVISORY — `dev:mock` sweep flaky on a cold `--force` server | **Left, with a ledger line** naming the cause (the spec's fixed 250 ms `settle` vs a cold dev server) and the surfaces (all outside Owns). | Accepted — it is my own finding and my own measurement, and the ledger's note is accurate. |

## New — round 2

| ID | Sev | file:line | What | Why | Fix |
|---|---|---|---|---|---|
| G1 | ADVISORY | `TypedSectionScreen.tsx:248` with `:136` | After a 409, `PublishBar`'s `error` shows `conflictNotice`; the first keystroke clears `conflictNotice` (`set()`), and since `publish.error` is still set until the next `mutate`, the raw `Configuration head revision changed from 41 to 44` reappears in its place. | Cosmetic and still truthful, but it reads as a *new* failure arriving as the operator starts fixing the old one. | Clear it together (`publish.reset()` in `set`, or fall back to `null` once a conflict has been acknowledged). |
| G2 | ADVISORY | `UndecidedKeysPanel.tsx:106-108` | The `!hasActiveRelease` branch — the sentence F1 was about — now has no test; both new fixtures pass a head revision. | The branch is right, and the button is already disabled in that state, so the exposure is small; but the one sentence this lease got wrong once is the one with no assertion on it. | One test with `head_revision` absent from the runtime mock. |

## Commands

```
$ npx vitest run                       # whole frontend suite, from frontend/
 Test Files  83 passed (83)
      Tests  1012 passed (1012)        # 1002 + the 10 new tests
   Duration  55.00s

$ npm run typecheck                    # tsc -b --pretty false
typecheck exit: 0
$ npm run lint                         # eslint . --max-warnings=0
lint exit: 0
```

## F1 — the live page, same drift response as round 1

```
$ curl -s http://localhost:8000/api/config/packaged-drift
RETURN_PLATFORM undecided= ['agents','clarification_policy','policy_evaluation',
  'return_eligibility_policy','return_policy','support_ingress'] would_adopt= [] filled= []
AI_GATEWAY undecided= [] would_adopt= [] filled= []
DEPENDENCY_SIMULATION undecided= [] would_adopt= [] filled= []

$ curl -s http://localhost:8000/api/config/runtime   ->  release publish-e20ae19b3dd0486a, head 111
```

`/config/overview` rendered from that response (text of the live page at `:5187`):

```
ACTIVE RELEASE            publish-e20ae19b3dd0486a
...
RETURN_PLATFORM: agents
The release's own value is kept for now. Taking the packaged file for this whole key
replaces every value the release holds for it.                      [Take packaged file]
RETURN_PLATFORM: clarification_policy          ... the same summary ...
RETURN_PLATFORM: policy_evaluation             ... the same summary ...
RETURN_PLATFORM: return_eligibility_policy     ... the same summary ...
RETURN_PLATFORM: return_policy                 ... the same summary ...
RETURN_PLATFORM: support_ingress               ... the same summary ...
WOULD ADOPT ON THE NEXT START
Nothing would be adopted from the packaged file right now.
```

Round 1 produced "No active release to compare against yet -- adopting is refused until one exists."
on every one of those six rows, under a card naming the active release. It is gone, the replacement
says what the merge actually did, and the consequence of the button is now stated on the row rather
than only inside the confirm dialog. The operator-decision keys (`policy_evaluation`,
`support_ingress`) still read the same as any other undecided key — that was never claimed as fixed,
and with the consequence now stated per row it is no longer the misleading half of round 1's F1.

## F2 — the gate, live, for a principal that *does* have `config.release.write`

```
$ /config/discovery, evaluated in the page
{ "fieldsets": [ { "cls": "flex flex-col gap-4 disabled:opacity-75", "disabled": false } ],
  "readOnlyNotice": false,
  "sample": [ {"label":"Ambiguity gap","disabled":false}, {"label":"Allow auto-confirmation","disabled":false},
              {"label":"Free-text fallback anchor","disabled":false}, {"label":"Strong anchors","disabled":false},
              {"label":"Field id*required","disabled":false}, {"label":"Intent key*required","disabled":false} ] }
```

One fieldset, wrapping the typed form, inert for a writer — the gate is real and costs the normal
path nothing. The withheld-`write` half is covered by the three new unit tests.

## The live e2e run, and the revert

```
$ E2E_REAL_BASE_URL=http://localhost:5187 npx playwright test --project=cfg4-e2e --workers=1 --reporter=list --timeout=60000
Running 4 tests using 1 worker
  ok 1 [cfg4-e2e] › e2e\config-discovery.spec.ts:31:3 › adds an item condition, publishes, ... then reverts (6.1s)
  ok 2 [cfg4-e2e] › e2e\config-fulfilment.spec.ts:26:3 › adds a bay-eligible status, ... then reverts (5.3s)
  ok 3 [cfg4-e2e] › e2e\config-overview.spec.ts:28:3 › reads the active release and renders the undecided-keys panel (1.9s)
  ok 4 [cfg4-e2e] › e2e\config-return-policy.spec.ts:26:3 › adds a freight keyword, ... then reverts (6.3s)
  4 passed (21.9s)
```

`GET /api/config/runtime` read by me before and after, independently of the specs:

```
head_revision before/after: 111 117          (six publishes: three edits, three reverts)
release       before/after: publish-e20ae19b3dd0486a publish-a02f3efc7aa54bcb
selection_vocabulary.conditions    identical: True | residue: []
return_policy...freight_keywords   identical: True | residue: []
bay.eligible_statuses              identical: True | residue: []
```

The `<fieldset>` F2 added does not get in the way of a writer: the same three specs still fill, add,
publish and revert through it.

## Judgement

Both blocking findings were fixed where the defect was, not where it showed. F1's replacement does
not merely delete the false sentence: it takes the "is there an active release" question away from
`would_adopt` — which cannot answer it, and whose inability to answer it was CFG-3a's own F1 — and
gives it to `headRevision`, which can; then it says what carry-forward actually did with the key and
what the button would do to it, which is the summary the brief asked for and the panel had never been
able to render, because `filled_leaves` is computed only for undecided keys and the old branch that
could show it was unreachable for them. The fixture in the new test is the live stack's own shape,
which is the right instinct after a finding that only the live stack exposed. F2 is fixed by the
mechanism already in the file next door rather than by threading a `disabled` prop through three
screens' worth of controls, and the three new tests pin the exact grant combination — `promote`
without `write` — that nothing covered before. Of the advisories, F3 is the one I would have accepted
in a weaker form and got a stronger one: the normalisation is now shared by both consumers of the
backend's path convention instead of duplicated, and its test asserts the error lands on card 0 and
*not* on card 1. F5's answer is careful about the thing that made it a finding at all — it refreshes
the head without touching the query cache, precisely because the cache is what remounts the editor —
and it is verified by a test that retries and checks the second call's revision, not just the notice.
The two advisories left are left with honest reasons and are both outside what this lease could fix
well. What remains is two small advisories of my own, one cosmetic and one a missing test for a
branch that is now correct. Nothing here is worth another round. PASS.
