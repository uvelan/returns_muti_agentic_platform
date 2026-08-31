# UNGATED — two guards that protect nothing

Append-only. One entry per step. Commands and output are pasted from the
terminal, not transcribed.

**Branch** `feat/ungated-guards`, branched from `refactor/unified-return-platform`
**by ref, not by sha** — the integration branch, not `master`.

    git rev-parse refactor/unified-return-platform
      772d6f5ba99001560eb1af230cf7b4d9fe482a2a
    git log --oneline -1 refactor/unified-return-platform
      772d6f5b docs: the citation chosen for resolvability did not resolve

    git worktree add -b feat/ungated-guards K:/Projects/Ret/rmap-ungated \
      refactor/unified-return-platform
      Preparing worktree (new branch 'feat/ungated-guards')
      HEAD is now at 772d6f5b docs: the citation chosen for resolvability did not resolve

The main worktree is on `feat/acc-frontend`, which is why the branch was cut in
a worktree of its own rather than in place.

---

## step:1 — the environment trap, verified rather than assumed

`backend/.venv` exists only in the **main** worktree, and
`return_platform_backend.pth` inside it appends that worktree's `src` to
`sys.path`. A bare interpreter call from here would therefore import
`feat/acc-frontend`'s code. Every Python command in this ledger pins
`PYTHONPATH`, and the pin was checked to actually win:

    cd K:/Projects/Ret/rmap-ungated/backend
    PYTHONPATH="K:/Projects/Ret/rmap-ungated/backend/src" \
      K:/Projects/Ret/returns_muti_agentic_platform/backend/.venv/Scripts/python.exe \
      -c "import sys, return_platform; print(...)"

    src entries: ['K:\\Projects\\Ret\\rmap-ungated\\backend\\src',
                  'K:\\Projects\\Ret\\returns_muti_agentic_platform\\backend\\src']
    resolved -> K:\Projects\Ret\rmap-ungated\backend\src\return_platform\__init__.py

The pin is **first** and the module resolves to **this** worktree. The `.pth`
entry is still on the path behind it, which is the state to re-check if a run
ever produces a result that belongs to another branch.

`backend/tests/conftest.py` refuses to configure without a repository-root
`.env`, which is gitignored. Provided the way `checks.yml` provides it:

    cp .env.example .env

---

## step:2 — item 1, the facts, before choosing a gate

The claim in the assignment is reproduced exactly:

    git grep -n "playwright\|test:e2e\|axe" .github/workflows/
    exit=1        (no output — nothing matches)

    ls .github/workflows/
    checks.yml  secret-scan.yml

The only axe sweep is `frontend/tests/canonical-routes.spec.ts:171`,
`test.describe("accessibility")` — one test per canonical route, asserting no
`critical` or `serious` violation across `wcag2a wcag2aa wcag21a wcag21aa`.

It is Playwright-only, and `frontend/vitest.config.ts` cannot reach it:

    include: [ "src/**/*.{test,spec}.{ts,tsx}" ]

`tests/canonical-routes.spec.ts` is outside `src/`, so `npm test` — the command
`frontend-tests` runs — does not collect it. `npm run test:e2e` exists in
`package.json` and is invoked by nothing.

**Rule 11 of the contracts makes accessibility a mandatory frontend outcome
gate.** So this is a gate the process requires and CI does not run.

---

## step:3 — item 2, the guard read against the file it is supposed to guard

`_GateProbe` is at
`backend/tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py:172`,
and its `all()` was a hand-written tuple of 18. Four `Worker(...)` sites in that
module are handed `probe.all()`.

The guard, `test_every_test_worker_registers_every_activity_the_workflow_calls`
in `backend/tests/test_return_case_workflow_replay_compatibility.py:496`, reads:

    absent = called - declared_activity_names(probe_class)

`declared_activity_names` reads `@activity.defn` declarations **off the class**.
It never looks at `all()`. Two readers, two lists, nothing asserting they agree.

Baseline, and the agreement that hides the hole:

    pytest tests/test_return_case_workflow_replay_compatibility.py -q
      17 passed in 6.35s

    declared count 18 | all() count 18
    equal as sets: True
    declared - all(): set()
    all() - declared: set()

---

## step:4 — the injection, reproduced here rather than inherited

`.plan/reviews/HARNESS-3.md` C1 records this hole. It is the justification for
the change, so it was rebuilt rather than cited. One entry removed from `all()`,
its `@activity.defn` left in place:

    removed "            self.case_has_return_details,\n" from all()
    grep -n 'activity.defn(name="case_has_return_details")'
      387:    @activity.defn(name="case_has_return_details")   <- decorator kept

    pytest tests/test_return_case_workflow_replay_compatibility.py -q
      17 passed in 4.50s

    declared 18 | all() 17
    declared - all(): {'case_has_return_details'}

**The guard is green while the worker under-registers.** A case scheduling
`case_has_return_details` would stop on a task nothing polls, and no test says
so. Confirmed, and reverted:

    git checkout -- backend/tests/acceptance/test_items_15_16_...py
    git diff --stat        (empty)

---

## step:5 — the fix, and the closure

One line and one import, the derived form that already exists in
`backend/tests/activity_probe.py` and that both sibling probes already use
(`test_return_case_policy_gate_real_infra.py:255`,
`test_return_case_workflow_real_infra.py:341`):

    +from tests.activity_probe import declared_activities
    ...
     def all(self) -> tuple[Any, ...]:
    -    return ( ...18 hand-written entries... )
    +    return declared_activities(self)

**Same 18 activities, still what `Worker` needs:**

    all() count: 18 | declared count: 18
    all() == the 18 that were hand-listed before: True
    all() == declared: True
    duplicates in all(): False
    every entry is a bound method of the probe: True

Order changes from workflow order to attribute-sorted, and that is not a
contract: `Worker` keys its activity registry by the decorator's `name=`, and
`declared_activities`' docstring states the sort exists so registration order
stops depending on definition order. The two sibling probes have run this way
already.

**The hole is closed.** The step:4 injection can no longer be expressed — there
is no list left to drop an entry from. The nearest injection that still exists
is dropping the decorator, and the guard now answers:

    removed '    @activity.defn(name="case_has_return_details")\n'

    pytest tests/test_return_case_workflow_replay_compatibility.py -q
    E  AssertionError: test workers under-register activities the workflow
       calls; each of these leaves a case scheduled on a task nothing polls:
       {'test_items_15_16_..._real_infra.py:612 (_GateProbe)': {'case_has_return_details'},
        '...:645 (_GateProbe)': {'case_has_return_details'},
        '...:704 (_GateProbe)': {'case_has_return_details'},
        '...:712 (_GateProbe)': {'case_has_return_details'}}
    1 failed, 16 passed in 5.22s

All four worker sites reported. Injection reverted, guard green again:

    17 passed in 4.28s

**Rule 13 — the gate that runs this.** The changed file is `live_infra`-marked
and deselected from the default suite by `addopts`; it is not its own gate, and
its module docstring says so. The gate is
`backend/tests/test_return_case_workflow_replay_compatibility.py`, which carries
no marker and runs in the default `pytest tests` that `checks.yml`'s `backend`
job invokes. That is the suite the red above was produced in.

    ruff check ...  -> All checks passed!
    ruff format --check ...  -> 1 file already formatted

Staged diff is the fix and nothing else — no whole-file CRLF rewrite:

    git diff --cached --stat
      ...tems_15_16_review_survives_a_kill_real_infra.py | 45 ++++++++++++----------
      1 file changed, 25 insertions(+), 20 deletions(-)
