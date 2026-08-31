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
