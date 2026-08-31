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
