# ACC phase 4 — frontend acceptance audit (items 24–25)

Append-only. One entry per step. Every command and its output is pasted from the
terminal, never transcribed from memory.

---

## step:00 — base verified by ref

The dispatch names the integration branch, not a sha, because trunk moved many
times today. Read the ref.

```
$ git fetch --all --prune
$ git rev-parse refactor/unified-return-platform
2d0e3d65e4005b32ef30c599fd57c302cd4a0ff9
$ git rev-parse origin/refactor/unified-return-platform
a50c5500788f99e909f23099a81731b37c736b8c
$ git rev-list --left-right --count refactor/unified-return-platform...origin/refactor/unified-return-platform
328	0
```

The **local** ref is 328 ahead of `origin` and 0 behind, so the local ref is the
tip. `origin/refactor/unified-return-platform` (`a50c5500`) is `base.sha` from
the T0 freeze — an agent that had branched from `origin/` would have been **328
commits behind** and would have silently omitted every merged slice. This is the
tenth stale-base near-miss on this run and the first where the *remote* copy of
the integration branch was the stale one.

The dispatch's own working tree arrived on `feat/acc-acid-b` at `38201a41`,
which is **19 commits behind** the tip:

```
$ git rev-list --left-right --count HEAD...refactor/unified-return-platform
0	19
```

Branched from the verified tip:

```
$ git checkout -b feat/acc-frontend 2d0e3d65e4005b32ef30c599fd57c302cd4a0ff9
Switched to a new branch 'feat/acc-frontend'
$ git rev-parse HEAD
2d0e3d65e4005b32ef30c599fd57c302cd4a0ff9
```

**Base sha: `2d0e3d65e4005b32ef30c599fd57c302cd4a0ff9`.**

Backend live-infrastructure suite: **not run, at any point on this branch**, per
the hard constraint. Nothing below invokes pytest.

---

## step:01 — the frontend suite before any change, and two things it says

`.nvmrc` asks for `24.18.0`; this workstation has `v24.14.0`.

```
$ node --version
v24.14.0
$ npm --version
11.1.0
```

Same major, same npm; recorded as a **degradation**, not waved past. Every figure
below is from `24.14.0`. CI (`.github/workflows/checks.yml`) runs the pinned
version, so the pipeline's numbers are the authority if they ever differ.

### (a) `npm test` as written does not complete on a loaded machine

```
$ npm test
...
⎯⎯⎯⎯⎯⎯ Unhandled Error ⎯⎯⎯⎯⎯⎯⎯
Error: [vitest-pool]: Failed to start forks worker for test files K:/Projects/Ret/returns_muti_agentic_platform/frontend/src/domains/config/SupportTemplateSection.a11y.test.tsx.
 ❯ node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3465:94
 ❯ Pool.schedule node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3465:5

Caused by: Error: [vitest-pool-runner]: Timeout waiting for worker to respond
 ❯ Timeout.<anonymous> node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3041:58
 ❯ listOnTimeout node:internal/timers:605:17
 ❯ processTimers node:internal/timers:541:7
...
 Test Files  40 passed (40)
      Tests  438 passed (438)
     Errors  21 errors
   Start at  15:53:10
   Duration  310.06s (transform 57.02s, setup 361.09s, import 120.27s, tests 10.14s, environment 393.62s)
```

**40 of 61 files ran. 21 never started.** The summary line reports
`40 passed (40)` — the denominator is the files that started, so the run reads
green in its headline while a third of the suite did not execute. The exit code
is the one thing that saves it:

```
$ npm test >/dev/null 2>&1; echo "EXIT=$?"
EXIT=1
```

Recorded as **FE-DEFECT-1** (see `frontend-audit.md`). It is directly relevant to
this dispatch: the machine is loaded precisely because another agent is running
the live suite, which is the contention under investigation.

Capping the pool makes the suite both complete and **four times faster**:

```
$ npx vitest run --maxWorkers=2
 Test Files  1 failed | 60 passed (61)
      Tests  2 failed | 858 passed (860)
   Start at  15:59:35
   Duration  79.18s (transform 2.94s, setup 23.59s, import 10.61s, tests 54.40s, environment 57.30s)
```

**Baseline figure for this branch: 61 files, 860 tests, 858 passed, 2 failed.**

### (b) the merge tip is red in the frontend suite, before an auditor starts

```
$ npx vitest run src/domains/registry.test.ts
 ❯ src/domains/registry.test.ts (14 tests | 2 failed) 18ms
     × declares exactly the canonical domains 6ms
     × shares a visibility capability only where that is deliberate 1ms

 FAIL  src/domains/registry.test.ts > the domain registry > declares exactly the canonical domains
AssertionError: expected [ Array(9) ] to deeply equal [ Array(8) ]

- Expected
+ Received

@@ -3,8 +3,9 @@
    "/approvals",
    "/config",
    "/graph-schema",
    "/operations",
    "/returns",
+   "/shipments",
    "/support",
    "/sync",
  ]

 FAIL  src/domains/registry.test.ts > the domain registry > shares a visibility capability only where that is deliberate
AssertionError: expected [ …(2) ] to deeply equal [ …(2) ]

- Expected
+ Received

  [
    "config.runtime.read: /config, /operations",
-   "returns.session.read: /returns, /support",
+   "returns.session.read: /returns, /shipments, /support",
  ]

 Test Files  1 failed (1)
      Tests  2 failed | 12 passed (14)
```

Working tree was clean when this ran (`git status --porcelain` empty), so this is
the **base commit's own state**, not anything ACC4 did. Provenance:

```
$ git log --oneline -5 -S'shipments' -- frontend/src/domains/registry.ts
14aa6915 test(tc-e2e-03): all seven gates green, and the console finally has a front door
$ git merge-base --is-ancestor 14aa6915 HEAD && echo "14aa6915 IS ancestor of base"
14aa6915 IS ancestor of base
```

Recorded as **FE-DEFECT-2**. Not repaired — ACC does not edit another track's
code, and the audit rule forbids touching a failing test. The consequence is
stated in `frontend-audit.md`: the `frontend-tests` CI gate is red on trunk, so
**every gate reading below is taken against a suite that is already failing**,
and the 2 failures are subtracted explicitly from every count rather than
absorbed into it.

Next: locate where each of the item 24–25 guarantees actually lives before
trusting any suite with it (predecessor finding 1).

---

## step:02 — where the item 24–25 guarantees actually live, and six injections

Predecessor finding 1 first: **check the suite before trusting it.** The map,
built by reading rather than by guessing from filenames:

| guarantee (plan line 279) | the file that pins it |
| --- | --- |
| conditional request is sent at all | `src/api/casePanel.test.ts` |
| 304 answered with no body; ETag moves/holds | `src/mocks/handlers/casePanelHandlers.contract.test.ts` |
| `private, no-cache` + `Vary` / `private, no-store` | `casePanelHandlers.contract.test.ts` (**not** `casePanel.test.ts`) |
| degraded ≠ empty | `support/supportPanelPayloads.test.ts`, `support/supportSections.test.tsx` |
| hash stability while a timer ticks | **nothing** |
| principal independence (identical body **and** ETag) | **nothing** |
| conflict presence: visible, blocking, cleared | **nothing** |

`grep -rn "conflict_present: true" src/` returns **one comment and no fixture**.
`grep -rni "principal" src/` over test files returns nine, none of them a panel
test. Those two rows are empty because the guarantees are untested, not because
the test is somewhere else.

### The injections

Every one applied to `frontend/src/`, the **whole** suite re-run at
`--maxWorkers=2`, then reverted. Baseline is `2 failed | 858 passed (860)`; the
2 are FE-DEFECT-2's pre-existing registry failures and are subtracted from every
reading below rather than absorbed.

**INJ-F2 — an unresolved conflict no longer blocks Send.**
`TemplateReviewSection.tsx:143`, `gaps.length > 0 || review.conflict_present` →
`gaps.length > 0`.

```
 Test Files  1 failed | 60 passed (61)
      Tests  2 failed | 858 passed (860)
```

**Not caught. Zero new failures.**

**INJ-F3 — the conflict banner never renders.**
`TemplateReviewSection.tsx:252`, `{review.conflict_present ? (` → `{false ? (`.

```
 Test Files  1 failed | 60 passed (61)
      Tests  2 failed | 858 passed (860)
```

**Not caught. Zero new failures.** Taken together with INJ-F2, the *entire*
conflict-presence surface — the marker's effect on the send control, the banner,
and the "Settle the other edit first." copy — is unpinned. Recorded as
**FE-HOLE-1**.

**INJ-F4 — the conditional request is never sent.**
`casePanel.ts:158`, `if (etag) headers.set("If-None-Match", etag);` removed.

```
 FAIL  src/api/casePanel.test.ts > reading the panel > revalidates with the ETag it holds and answers from the cache on 304
 Test Files  2 failed | 59 passed (61)
      Tests  3 failed | 857 passed (860)
```

**Caught.** Verdict **A**.

**INJ-F5 — `Vary: Authorization` dropped from `/panel`.**

```
 FAIL  src/mocks/handlers/casePanelHandlers.contract.test.ts > the panel mock serves the conditional read the contract is built on > declares the cache headers the contract fixes, on both surfaces
 Test Files  2 failed | 59 passed (61)
      Tests  3 failed | 857 passed (860)
```

**Caught.** Verdict **A**.

**INJ-F6 — `isDegraded` returns `false` always.**
`supportPanelPayloads.ts:584`.

```
 FAIL  src/domains/returns/panes/casePanel/support/supportPanelPayloads.test.ts > degradation, and the ids both registries key on > tells a section that could not be read from one with nothing to say
 FAIL  src/domains/returns/panes/casePanel/support/supportSections.test.tsx > the return-record cards > tells a section it could not read from a case with nothing to say
 Test Files  3 failed | 58 passed (61)
      Tests  4 failed | 856 passed (860)
```

**Caught**, by two tests. Verdict **A** — "degraded is a display state, not an
absence" holds.

### INJ-F7, three attempts, and the finding is in the difference between them

The guarantee is *two polls with no state change **while a timer ticks** produce
an identical ETag*. Finding the line that **is** that mechanism took three tries,
and the first two are recorded because the reasoning is the result.

**INJ-F7a — add a computed countdown to the panel body.**
`template_review_seconds_remaining: Math.floor((Date.parse(DEADLINE_ISO) - Date.now())/1000)`.

```
 FAIL  src/mocks/handlers/casePanelHandlers.contract.test.ts > every case-panel mock body conforms to the schema it claims > get /api/v1/cases/:caseId/panel
```

Red — but on **schema conformance**, because `CasePanelView` is
`additionalProperties: false` and the key is undeclared. Neither ETag-stability
test moved. **DISCARDED**: it measures the contract gate, not the hash.

**INJ-F7b — put the wall clock on a field the schema already declares.**
`template_review_reminders_sent: Math.floor(Date.now()/1000) % 4`.

```
 FAIL  src/domains/returns/panes/casePanel/CasePanel.test.tsx > what the associate sees > shows the draft, its provenance and the deadline
```

Red — but on a **fixture-value assertion** ("1 of 3 reminders"), which exists
only because that number happens to be on screen. Both ETag-stability tests
again stayed green. **DISCARDED**: caught incidentally, by a guard that would
not exist for any field nobody renders.

**INJ-F7c — hash the envelope instead of the data.**
`etagFor(body.data)` → `etagFor(body)`; the envelope's `generated_at` is a
wall-clock value.

```
 FAIL  src/mocks/handlers/casePanelHandlers.contract.test.ts > … > answers 304 with no body when the ETag still matches
 FAIL  src/mocks/handlers/casePanelHandlers.contract.test.ts > … > moves the ETag when the panel moves, and holds it when nothing does
 Test Files  2 failed | 59 passed (61)
      Tests  4 failed | 856 passed (860)
```

**Caught**, by both stability tests. Verdict **A** — but a **bounded** A, and the
bound is the finding:

> `moves the ETag when the panel moves, and holds it when nothing does` issues
> its two reads **back to back**. It therefore pins stability against a
> *millisecond*-resolution clock leak and **cannot** pin it against a
> second-or-coarser one — which is the only resolution a real 10-second poll
> would ever expose. F7b is the proof: a value that changes once per second was
> invisible to both stability tests and was caught by an unrelated fixture
> assertion.

The contract's own wording — *"two polls with no state change **while a timer
ticks**"* — names the case the test omits. Recorded as **FE-HOLE-2**.

### FE-DEFECT-3 — AMENDMENT-6 was ruled and never executed

Found while mapping, not by injection. AMENDMENT-6 (`22e1aca6`) retires
`support_digest`, `clarifications` and `parked_messages` from `CasePanelView`
because a registered section cannot write a top-level field. All three are still
there, in all four places:

```
$ node -e "const d=require('./openapi/return-platform.openapi.json');console.log(Object.keys(d.components.schemas.CasePanelView.properties))"
[
  'accepted_commands', 'case_id',   'clarifications',
  'execution',         'parked_messages', 'return_records',
  'reviews',           'sections',  'support_digest',
  'timers'
]
$ grep -n "support_digest\|clarifications\|parked_messages" backend/src/return_platform/operations/case_panel.py
205:    support_digest: tuple[dict[str, Any], ...] = ()
206:    clarifications: tuple[dict[str, Any], ...] = ()
208:    parked_messages: int = 0
$ grep -n "support_digest\|parked_messages" frontend/src/mocks/handlers/casePanelHandlers.ts
216:    support_digest: [],
224:    parked_messages: 0,
$ git log --oneline -S'support_digest' -- backend/src/return_platform/operations/case_panel.py
32e92df5 (V1) step:15 the panel, the endpoints, and four ways the draft was silently broken
```

One commit ever touched them — the one that added them. **Nothing removed
them**, and the V1 comment AMENDMENT-6 quotes as *"a connection that does not
exist"* is still in `api/case_panel.py:105-111` word for word. Reported, not
repaired: this is V1/V3's DTO and the T0 retirement is theirs to land.

Tree clean after every revert:

```
$ git status --porcelain
$ echo "TREE CLEAN"
TREE CLEAN
```

---

## step:03 — FE-HOLE-1 closed, and injected against three ways

Two more locating results first, both by reading:

**Parked-message entry — verdict A, and comfortably so.** INJ-F8:
`readParkedPayload`'s `count: readCount(section?.payload, "count") ?? 0` →
`count: 0`, so the section returns `null` and the entry vanishes.

```
 FAIL  src/domains/returns/panes/casePanel/support/supportPanelIntegration.test.tsx > … > tells an operator that a message was parked, and why, and that it is safe
 FAIL  src/domains/returns/panes/casePanel/support/supportPanelPayloads.test.ts > the parked-messages entry > takes the count the contributor gave
 FAIL  src/domains/returns/panes/casePanel/support/supportPanelPayloads.test.ts > the parked-messages entry > reads a contributed parked entry, in the payload convention
 FAIL  src/domains/returns/panes/casePanel/support/supportPayloadCasing.test.ts > what the readers ask a contributed payload for > asks the parked section for exactly these keys, all camelCase
 FAIL  src/domains/returns/panes/casePanel/support/supportPayloadCasing.test.ts > a payload sent in the wrong convention > reads as nothing -- the reader does not quietly translate it
 FAIL  src/domains/returns/panes/casePanel/support/supportSections.test.tsx > the parked-messages entry > names the count and says the messages are safe
 FAIL  src/domains/returns/panes/casePanel/support/supportSections.test.tsx > the parked-messages entry > asserts no cause when the contributor did not give one
 FAIL  src/domains/returns/panes/casePanel/support/supportSections.test.tsx > what arrives while somebody is typing > announces a message being parked as its own event
 Test Files  5 failed | 56 passed (61)
      Tests  10 failed | 850 passed (860)
```

**Caught by eight tests.** The stream-order half of that scenario is the copy —
*"will be read in the order they came in"* (`supportSections.tsx:360`) — and the
reprocessing it promises is backend behaviour, not reachable from here. See
"what was not reached".

**`CaseOperationsPage` reads the same payload — verdict A, structurally.** No
injection, because there is nothing to break: there is exactly one fetch of the
panel in the whole app and exactly one query for it, and the operations page
mounts the copilot's own component.

```
$ grep -rn "/panel\`" --include=*.ts --include=*.tsx src/ | grep -v mocks/ | grep -v generated
src/api/casePanel.ts:162:    response = await fetch(`/api/v1/cases/${encodeURIComponent(caseId)}/panel`, { headers });
$ grep -rn "casePanelApi.read" --include=*.tsx src/ | grep -v test
src/domains/returns/panes/casePanel/CasePanel.tsx:48:    queryFn: () => casePanelApi.read(caseId),
$ grep -n "CasePanel" src/domains/operations/CaseOperationsPage.tsx
22:import { CasePanel } from "../returns/panes/casePanel/CasePanel";
```

One reader, one component, two mount points. A test could only re-assert the
import graph, and "same payload" holds by construction — which is stronger than
a test, and is recorded here so a later reader does not add a second fetch
believing the guarantee is only conventional.

### The guard: `TemplateReviewSection.test.tsx`, 5 tests

New file. Covers the marker being **visible**, its effect on **Send**, and its
being **cleared by the canonical-edit write** — the three things INJ-F2/F3
showed nothing watched.

Its premise helper is the point:

```ts
function expectsConflictOnly(conflictPresent: boolean) {
  const only = review(conflictPresent);
  expect(only.gaps).toEqual([]);
  expect(only.draft.gaps).toEqual([]);
  expect(only.conflict_present).toBe(conflictPresent);
  expect(only.state).toBe("OPEN");
}
```

`blocked` is `gaps.length > 0 || review.conflict_present`. A conflicted fixture
that also carried a gap is blocked either way, and every assertion would pass
with the conflict limb deleted — **the same vacuity ACC3 found in the backend's
delivery tests, in its frontend form.** Asserting the gaps are empty first makes
that impossible; a later edit that adds a gap to the fixture fails the premise
rather than quietly disarming the test.

```
$ npx vitest run src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx
 Test Files  1 passed (1)
      Tests  5 passed (5)
```

### Injected against — three times, because the rule is that a strengthening is a blind test until it is

**INJ-F2 re-run** (`blocked` loses its conflict limb):

```
 FAIL  src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx > what an unresolved conflict does to Send > blocks it, and names the conflict as the reason rather than a missing field
 FAIL  src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx > clearing it > is done by the canonical-edit write, and the panel then unblocks
 Test Files  2 failed | 60 passed (62)
      Tests  4 failed | 861 passed (865)
```

Previously **zero**. Now two.

**INJ-F3 re-run** (banner suppressed):

```
 FAIL  src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx > a conflict on the shared panel > is on the screen, in words about the other person rather than about a flag
 FAIL  src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx > clearing it > is done by the canonical-edit write, and the panel then unblocks
 Test Files  2 failed | 60 passed (62)
      Tests  4 failed | 861 passed (865)
```

Previously **zero**. Now two.

**INJ-F10 — "Keep this version" clears the banner without writing the canonical
edit.** The third test would be worth little if it only re-asserted the banner;
this checks it pins the *write*.

```
     × is done by the canonical-edit write, and the panel then unblocks 1105ms
 Test Files  1 failed (1)
      Tests  1 failed | 4 passed (5)
```

**One red, and the other four green** — which is the measurement proving INJ-F10
is invisible to them and that this test, alone, owns the write.

Business consequence of what FE-HOLE-1 left open, stated at its size: two
associates open the same case, one saves an edit, and the second is shown no
banner and a live Send button. The backend still refuses the approval — sect. 6
rejects unresolved conflicts at the CAS — so nothing wrong is *sent*. What is
lost is the whole point of the marker: the associate is handed a bare refusal
for a condition the screen was supposed to have told them about and offered a
one-press way out of, and presses Send again.

`git status` after every revert shows only the new test file:

```
$ git status --porcelain
?? frontend/src/domains/returns/panes/casePanel/TemplateReviewSection.test.tsx
```

**Gate (rule 13): `frontend-tests` (`npm test`) in `.github/workflows/checks.yml`.**

---

## step:04 — the two guarantees with no test at all

Both added to `casePanelHandlers.contract.test.ts`, which is where the map says
the sibling ETag and cache-header guarantees live. Putting them anywhere else
would have created exactly the mis-pointed row ACC3 spent its dispatch finding.

### A wrong assertion first, kept because it is the useful part

Both tests were written against the **whole HTTP response** and both failed:

```
AssertionError: expected '{"data":{"case_id":"case-mock-2026","…' to be '{"data":{"case_id":"case-mock-2026","…'
```

— on `meta.generated_at` and nothing else. That stamp *must* differ between two
reads; it says when the reply was composed. The digest is taken over
`body.data` for precisely that reason (`etagFor(body.data)`), and sect. 9's
"identical body" is a claim about the composed `CasePanelView`.

A red that **looks** like a principal-independence failure and is nothing of the
kind. The boundary is now a named helper, `panelBytes`, whose docstring carries
this so the next reader gets the answer without repeating the run.

### Both injected against

**FE-HOLE-2 closed — `holds the ETag across a real wall-clock second, with the
deadline ticking`.** Two premises asserted before the comparison: the deadline
is present and in the future (a payload with no timer satisfies "no wall-clock
value" trivially), and at least 1,000 ms of *real* time passed between the reads
(a mocked clock would only prove the mock held still).

**INJ-F7b re-run** — `template_review_reminders_sent: Math.floor(Date.now()/1000) % 4`:

```
     × holds the ETag across a real wall-clock second, with the deadline ticking 1214ms
 Test Files  1 failed (1)
      Tests  1 failed | 19 passed (20)
```

**One red, nineteen green.** The nineteen include both pre-existing stability
tests, which is the measurement: at step:02 this same injection was invisible to
them and was caught only by an unrelated fixture assertion in another file.

**Principal independence closed — `serves two principals the same bytes and the
same ETag, commands included`.** The trap avoided: a panel carrying nothing
attributable to any actor is identical for two principals the way an empty room
is. So the test **approves first**, seeding an `accepted_commands` entry — which
sect. 9 names as the field that stays unfiltered — and asserts it is there
before comparing. It also asserts, from the server's own view of the requests,
that two genuinely different `Authorization` values arrived:

```ts
expect(seen).toEqual(["Bearer principal-one", "Bearer principal-two"]);
```

on what the handler saw, not on what the test passed. A fetch layer that dropped
the header would otherwise turn the whole test into a comparison of one
principal with itself.

**INJ-F11** — the panel filters `accepted_commands` to the requesting principal,
which is the defect sect. 9 names. Run against the **whole** suite, not just its
own file:

```
 FAIL  src/domains/registry.test.ts > … (pre-existing, 2)
 FAIL  src/mocks/handlers/casePanelHandlers.contract.test.ts > … > serves two principals the same bytes and the same ETag, commands included
 Test Files  2 failed | 60 passed (62)
      Tests  3 failed | 864 passed (867)
```

**Exactly one test in 62 files catches it, and it is the new one.** Before this
commit: none.

### The suite, reverted and clean

```
$ git checkout -- src/mocks/handlers/casePanelHandlers.ts
$ git status --porcelain
 M frontend/src/mocks/handlers/casePanelHandlers.contract.test.ts
$ npx vitest run --maxWorkers=2
 FAIL  src/domains/registry.test.ts > the domain registry > declares exactly the canonical domains
 FAIL  src/domains/registry.test.ts > the domain registry > shares a visibility capability only where that is deliberate
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
   Duration  75.43s
```

The only modified file is a test file. **Before: 61 files / 860 tests / 858
passed. After: 62 files / 867 tests / 865 passed.** The 2 failures are
FE-DEFECT-2's and are the same two throughout.

**Gate (rule 13): `frontend-tests` (`npm test`).**

---

## step:05 — the outcome gates, with evidence rather than an assurance

Contracts ruling 11 and §3's frontend-outcome-gate rule. Every named skill used
here **was available**; no degradation to record on availability.

### The static gates — `frontend-static` and `contracts`

```
$ npm run lint
> eslint . --max-warnings=0
$ npm run typecheck
> tsc -b --pretty false
```

Both clean. The first run was **not**:

```
K:\…\src\domains\returns\panes\casePanel\TemplateReviewSection.test.tsx
  276:29  error  Async arrow function has no 'await' expression  @typescript-eslint/require-await
```

An MSW resolver declared `async` with nothing to await. Fixed by dropping
`async`, not by relaxing the rule.

```
$ npm run contracts:check
> npm run contracts:generate && npm run contracts:served && git diff --exit-code openapi/return-platform.openapi.json src/api/generated/return-platform.d.ts
🚀 openapi/return-platform.openapi.json → src/api/generated/return-platform.d.ts [501.5ms]
Fully-required schemas verified against the published document: CaseFactProjection (11)
```

Clean, **including the `git diff --exit-code`**. That last part matters more
than it looks: it means the committed OpenAPI is **byte-identical to what the
backend generates today**, which converts FE-DEFECT-3 from "the document might
be stale" into a measured fact — `clarifications`, `parked_messages` and
`support_digest` are on the live backend DTO, and AMENDMENT-6's retirement has
not been executed anywhere.

### MSW conformance

`casePanelHandlers.contract.test.ts` is the conformance gate and it runs
green (20/20). Its own strength was measured rather than assumed: INJ-F7a added
an undeclared key to the mock panel body and it reddened on
`additionalProperties: false`, so the document→mock direction genuinely bites.

### Token reuse — `design:design-system`, met by injection

`review.conflict` is `bg-tertiary-container/40 … text-on-surface`, an M3 role
pairing with no hex. It is measured, not asserted, by
`reviewContrast.test.ts:297`, which requires **≥ 7:1** — and that file exists
because `review.conflict` originally shipped at **1.29:1** and was found by an
audit rather than by a gate. It also has its own vacuity guard
(*"computes real ratios -- the helper is not agreeing with itself"*).

Worth recording for the map: `supportTokens.test.ts` is scoped to the `support`
group **by an explicit decision in its header**, so it does not cover `review`.
That is not a gap — `reviewContrast.test.ts` covers `review` under its own
ownership — but a reader who found only the first file would conclude wrongly.

### Accessibility — `design:accessibility-review`, WCAG 2.1 AA

Skill invoked; audit run against the conflict surface
(`TemplateReviewSection.tsx` banner ~252, "Keep this version", Send ~590).

| criterion | finding | evidence |
| --- | --- | --- |
| 1.1.1 non-text | `<Users aria-hidden="true">` — decorative icon correctly hidden | source |
| 1.4.3 contrast | `review.conflict` ≥ 7:1, against AA's 4.5:1 | `reviewContrast.test.ts:297`, gated by `frontend-tests` |
| 2.1.1 / 2.4.3 keyboard | Send uses `aria-disabled`, **never `disabled`**, so it stays in the tab order and can be discovered | **INJ-F12** |
| 3.3.1 error identification | the block names its cause in words — "Settle the other edit first." — not a code | new test |
| 4.1.2 name/role/value | Send announces disabled **and** carries `aria-describedby="send-blocked-reason"` | **INJ-F12** |

**INJ-F12** — `aria-disabled={busy || blocked}` + `aria-describedby` replaced by
`disabled={busy || blocked}`:

```
 FAIL  src/domains/returns/panes/casePanel/CasePanel.test.tsx > the keyboard path: review, edit, send > keeps a blocked Send focusable and says why
 FAIL  …/TemplateReviewSection.test.tsx > what an unresolved conflict does to Send > blocks it, and names the conflict as the reason rather than a missing field
 FAIL  …/TemplateReviewSection.test.tsx > what an unresolved conflict does to Send > leaves Send pressable once nothing is in conflict
 FAIL  …/TemplateReviewSection.test.tsx > clearing it > is done by the canonical-edit write, and the panel then unblocks
 Test Files  3 failed | 59 passed (62)
      Tests  6 failed | 861 passed (867)
```

Pinned already on the **gap** limb by `CasePanel.test.tsx`; now pinned on the
**conflict** limb too. Verdict **A**.

Two a11y findings, both reported and neither repaired:

**FE-DEFECT-5 — the a11y sweep item 24–25 asks for is gated by nothing.** The
repository's only axe run is `frontend/tests/canonical-routes.spec.ts`, a
Playwright spec.

```
$ grep -rn "playwright\|test:e2e" .github/workflows/*.yml
$ (no output)
```

No workflow runs Playwright at all, and `vitest`'s `include` is `src/**`, which
does not match `tests/*.spec.ts`. `reviewContrast.test.ts` states this for its
own token; the grep generalises it: **the sweep is not executed by CI on any
push.** Same shape as STATUS's "a guard with no gate", in the a11y plane.

**FE-DEFECT-6 — a conflict appearing mid-draft is never announced.** The banner
arrives on a background poll and carries no `role="status"` / `aria-live`. This
console already has the pattern and uses it deliberately elsewhere — the
announcer in `supportSections.tsx` exists precisely to tell "whoever is not
looking at the screen" that an artifact arrived or a message was parked — and
its signature is `artifacts|unbound|parked` with **no conflict term**. So a
screen-reader associate typing into a draft learns about the other person's
edit only when Send refuses. Not invented against an outside standard: measured
against the project's own established pattern.

### UX copy — `design:ux-copy`

The conflict path's three strings, judged against §3's list (send confirmation,
"support is asking you this", do-not-mix, empty/error states):

* *"Somebody else is editing this draft"* — names the person, not the flag.
* *"Their wording is not shown here — it is theirs until it is agreed."* — the
  load-bearing one. §6 keeps private edit contents out of the shared payload, so
  there is genuinely nothing to show; without this sentence the associate hunts
  for text that does not exist. **This is the empty-state copy for a deliberate
  absence**, and it is now asserted rather than admired.
* *"Settle the other edit first."* — an instruction with a verb, distinct from
  the gap limb's *"Fill the missing details first."* The distinctness is what
  makes the conflict limb observable; the new test asserts the presence of one
  **and the absence of the other**.

### Code review — `engineering:code-review`

Skill invoked on the ACC4 diff (test files only, 462 insertions, no production
code). Two findings raised against my own work, **both acted on**:

1. **An overstated justification.** The wall-clock test's comment claimed a
   mocked clock "would only prove the mock held still". Not true — a fake clock
   *would* detect the leak. The honest reason for real time is that fake timers
   and MSW's `delay()` must be reconciled (`shouldAdvanceTime`) before a request
   resolves at all, which makes the result depend on that reconciliation. A
   comment that reads like the mechanism but is not one is the exact defect this
   audit keeps finding; corrected rather than left.
2. **An unasserted precondition.** The principal test's seeding approve had its
   status unchecked; a 409 would have surfaced two screens later as an empty
   `accepted_commands`. `expect(seeded.status).toBe(200)` added — premise 2 would
   still have caught it, but now the failure says where.

Noted and **not** changed: the `removeAllListeners` call is not in a `finally`,
so a throw between registration and removal leaks a listener. It matches the
existing pattern in `casePanel.test.ts:58`, and the listener only appends to a
closed-over array that is then discarded, so the blast radius is nil. Recorded
so the choice is visible rather than accidental.

### Re-verified after the review edits

```
$ npm run lint     (clean)
$ npm run typecheck (clean)
$ npx vitest run --maxWorkers=2
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
```

And both guards re-injected together, to prove the review edits did not soften
them:

```
     × holds the ETag across a real wall-clock second, with the deadline ticking 1203ms
     × serves two principals the same bytes and the same ETag, commands included 156ms
 Test Files  1 failed (1)
      Tests  2 failed | 18 passed (20)
```

**Gates (rule 13):** the two new/extended test files run in **`frontend-tests`**
(`npm test`); `lint`/`typecheck` in **`frontend-static`**; the OpenAPI and MSW
conformance in **`contracts`**. Every guard ACC4 adds is executed on every push.
