# HARNESS-3 — RV review

**Branch** `feat/live-harness-registration`
**Head** `1f0dda3d` — *"(harness) step:28 the merge tree is green: 5247 passed, and both guards still bite"*
**Merge-base with trunk** `bf7fa140`
**Trunk at review** `16868eaa` — one commit ahead of the `bf7fa140` the branch merged
**Merge tree** `84961b19` (`git merge-tree --write-tree refactor/unified-return-platform feat/live-harness-registration`, clean)
**Round 1** `HARNESS-1.md` `CHANGES_REQUIRED` · **Round 2** `HARNESS-2.md` `CHANGES_REQUIRED` *(verdict withdrawn from `PASS`)*

# Verdict: `PASS`

Zero unresolved findings. F4, F5 and F6 are fixed, and the fix to F6 surfaced
and closed a real defect on trunk (the branch calls it F7). Every claim I was
asked to check hardest was checked by execution, not by reading — including the
two that would have been the branch's own failure modes had they gone the other
way.

One correction to the record and one named follow-up are at the end. Neither is
a finding, and I say so explicitly for each.

---

## 0. The standing rule, applied to myself

HARNESS-2's closing rule was mine, and it is the rule this round exists for:
**a guard whose outcome depends on repository state outside its own diff must
have its merge tree built and RUN, not merely merged.** Round 2's failure was
that I had the merge tree materialised and ran `ruff` over it instead of
`pytest`.

So this round begins with the suite, against **current** trunk rather than the
trunk the author merged:

    git merge-tree --write-tree refactor/unified-return-platform feat/live-harness-registration
      -> 84961b19   (clean, no conflicts)

    git archive 84961b19 | tar -x -C <scratch>/mt3
    cd <scratch>/mt3/backend
    PYTHONPATH=<scratch>/mt3/backend/src python -m pytest tests -q

    5247 passed, 11 skipped, 514 deselected, 2 warnings in 289.92s (0:04:49)

**The `PYTHONPATH` pin was verified to beat the `.pth`, not assumed to.**
`backend/.venv/.../return_platform_backend.pth` adds the main worktree's `src`;
under the pin the interpreter resolves the merge tree first:

    sys.path src entries: ['<scratch>/mt3/backend/src',
                           'K:\\Projects\\Ret\\returns_muti_agentic_platform\\backend\\src']
    return_platform.__file__ -> <scratch>/mt3/backend/src/return_platform/__init__.py

The merge tree needed the repository's untracked root `.env` copied in
(`conftest.py:30` refuses to configure without it); that is the only thing added
to the archived tree.

## 1. The arithmetic reconciles, and it reconciles the right way

| run | failed | passed | skipped | deselected | total collected |
|---|---|---|---|---|---|
| HARNESS-2, unfixed merge | 2 | 5245 | 11 | 514 | 5258 |
| this round, merge tree `84961b19` | **0** | **5247** | **11** | **514** | **5258** |

`5245 + 2 = 5247`, **skips and deselections identical**, and the collected total
is unchanged at 5258. That is the shape a repaired guard has to have. A suite
that *gained* tests while fixing a guard would need explaining, and one that
*lost* skips would be hiding something; neither happened. The two rows that
moved are the branch's own guard, and I confirmed that from the JUnit report
rather than from the headline:

    tests.test_return_case_workflow_replay_compatibility
      ::test_a_test_worker_for_the_case_workflow_exists_to_be_checked          -> pass
      ::test_every_test_worker_registers_every_activity_the_workflow_calls     -> pass

Measured on a trunk one commit *newer* than the one the author merged, so the
number is not inherited from the author's run.

## 2. The merge is a genuine three-way merge

This matters because HARNESS-2 §6's ruling on `ruff format` is valid only for a
three-way merge, and because the branch's own justification for merging at all
rests on the distinction.

- `4ed01b4f` has **two parents**: `6166df15` (branch) and `bf7fa140` (trunk).
- Its tree is **exactly** what `git merge-tree --write-tree 6166df15 bf7fa140`
  produces — `3782735212e0…` both ways. No post-merge tinkering rode along.
- The merge took **trunk's** side on the pre-existing format defect, which is
  the behaviour only a three-way merge has. `draft_support_request` was wrapped
  across three lines at the merge-base and on the branch, collapsed on trunk,
  and is collapsed in the result:

      git diff 2f1c0e50 1f0dda3d -- <both probe files>
        -    async def draft_support_request(
        -        self, request: DraftSupportRequestInput
        -    ) -> SupportRequestDraft:
        +    async def draft_support_request(self, request: DraftSupportRequestInput) -> SupportRequestDraft:

  Confirmed on the merged tree with the repository's own ruff:

      ruff format --check .  ->  1160 files already formatted
      ruff check .           ->  All checks passed!

**And the structural necessity claim is true.**
`backend/tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py`
does **not** exist at `6166df15` (`git cat-file -e` fails) and **does** exist at
`bf7fa140`. F7 genuinely could not be expressed until the merge existed. The
merge is not a convenience.

## 3. F7 — fixed at the right layer, both halves

This was the finding to check hardest, and the author's framing of the trap is
correct.

**The two readers are different.** `activity_probe.declared_activity_names()`
reads `@activity.defn` declarations off the class. `_GateProbe.all()` is a
**hand-written tuple** — verified by reading it, not by taking the claim — and
`all()` is what every `Worker(...)` in that module is handed. So the guard and
the worker read different lists, and **adding only the decorators would have
turned the guard green while leaving the worker still not registering the three
activities.** That is the exact defect this whole track began with, and it would
have been reproduced inside its own repair.

**Both halves are in the diff** (`f75df769`, 42 insertions, 0 deletions):

    + two imports (ClarificationAnswerResult, ClarificationRelayView)
    + @activity.defn(name="case_has_return_details")        + method
    + @activity.defn(name="record_clarification_answer")    + method
    + @activity.defn(name="relay_clarification_to_support") + method
    + self.case_has_return_details,        <- into all()
    + self.record_clarification_answer,    <- into all()
    + self.relay_clarification_to_support, <- into all()

**And `all() == declared` at 18/18, measured on the merge tree:**

    declared count 18 · all() count 18 · equal as sets: True
    declared - all(): set()   all() - declared: set()
    declared_activity_names(_GateProbe): 18

No scenario, assertion, timing or fixture in that file was touched. The diff is
insert-only.

## 4. F5 — the subset pin still bites, and it bites alone

Three injections into a scratch copy of the merge tree, each reverted and the
tree verified byte-identical to `84961b19` afterwards (I edit no source).

**(a) A pinned file moved away, caught by name.** Renaming
`test_return_case_policy_gate_real_infra.py` to `moved_policy_gate_real_infra.py`
leaves the worker *count* unchanged, so the floor cannot fire:

    E  AssertionError: assert {'test_return...eal_infra.py'} <= {'moved_polic...eal_infra.py'}
    E    Extra items in the left set: 'test_return_case_policy_gate_real_infra.py'
    1 failed, 16 passed

**(b) A pinned file that stays but stops registering the workflow — the floor
fires first, exactly as the author reports.** Rewriting that file's nine
`workflows=(ReturnCaseWorkflow,)` to another workflow:

    E  AssertionError: expected the real-infra suites' workers, found 18
    E  assert 18 >= 20

**(c) The same injection with the floor lowered to `>= 0` — the subset assertion
answers on its own.**

    E  AssertionError: assert {'test_return...eal_infra.py'} <= {'test_items_...eal_infra.py'}
    E    Extra items in the left set: 'test_return_case_policy_gate_real_infra.py'

The isolation is genuine. **Two nets that only ever fire together are one net
with a spare**, and these are two: (a) is caught by the name pin with the floor
intact, (c) is caught by the name pin with the floor neutralised. The residual
the comment declares — a newly-added worker file is unprotected until somebody
names it — is real, stated in the test's own comment, and is the trade
HARNESS-2 §"Judgement 1" asked for.

## 5. F6 — module-path derivation extends coverage rather than restoring it

The argument against scoping the walker, made concrete. I injected a single
dropped activity into the **subdirectory** probe (removing one
`@activity.defn` decorator from `_GateProbe` while leaving the method and the
`all()` entry in place) and ran the registration guard twice on the same
injection.

**With the derived importer (this branch):**

    E  test workers under-register activities the workflow calls; each of these
       leaves a case scheduled on a task nothing polls:
       {'test_items_15_16_..._real_infra.py:608 (_GateProbe)': {'case_has_return_details'},
        '...:641 (_GateProbe)': {'case_has_return_details'},
        '...:700 (_GateProbe)': {'case_has_return_details'},
        '...:708 (_GateProbe)': {'case_has_return_details'}}

**With the old importer (`importlib.import_module(f"tests.{path.stem}")`),
same injection, everything else identical:**

    E  ModuleNotFoundError: No module named 'tests.test_items_15_16_review_survives_a_kill_real_infra'
    1 failed in 2.17s

**Confirmed.** The old code did not report a weaker finding — it produced **no
finding of any kind**, crashing on the import before it could look at the probe
at all. Detection in `backend/tests/`' subpackages is new coverage, not restored
coverage, and scoping the walker would have deleted it. HARNESS-2's
"Judgement 2" is sustained on evidence rather than on argument.

## 6. The recorded failed injection — the reasoning is right, and I reran it

The author reports building an injection that produced no finding — renaming the
Python method while leaving `@activity.defn(name=...)` intact — and concluding
**its injection was wrong, not the guard**. I reproduced it: renamed
`case_has_return_details` to `renamed_method_xyz`, kept the decorator's `name=`,
updated the `all()` entry to the new attribute:

    17 passed in 4.56s

**The conclusion is correct, and it is correct for a stronger reason than "the
guard reads the decorator's name."** That mutation is *behaviour-preserving*.
The worker still registers under `case_has_return_details`; the workflow's
`execute_activity("case_has_return_details", …)` still resolves; no case wedges.
The property under test is not violated, so a guard that went red here would be
producing a **false positive** — it would be pinning the probe's Python
identifiers, which are nobody's contract. `activity_probe.py:88-102` is explicit
about this (*"the decorator's `name=` argument, not the Python attribute — those
are the names a worker registers under and the names the workflow asks for, and
they are allowed to differ"*), and that is the right thing for it to read.

A wrongly-exonerated guard would be worse than a missing injection. This guard
is rightly exonerated: the injection did not construct the defect it was aiming
at.

## 7. Ownership and integrity

**The crossing into `backend/tests/acceptance/` — acceptable, and I rule it so
deliberately.** Rule 11 is a `HALT` rule and I considered it. It does not fire
here:

- HARNESS-2 §F6 set out the two permitted routes explicitly — *"either ACC adds
  the three stubs first, or the two land together"* — and forbade the third
  (*"what must not happen is the guard being scoped down to make the red go
  away"*). The branch took the second permitted route.
- The crossing is **declared** in the most visible places: its own commit
  (`f75df769`), that commit's message, and ledger step:27. Rule 11's target is
  the undeclared edit and the neighbouring contract altered to compile; this is
  neither.
- It is **minimal and insert-only** — 42 insertions, 0 deletions, no scenario,
  assertion, fixture or timing touched.
- **No live ACC branch conflicts with it.** `feat/acc-harness`,
  `feat/acc-scenarios` and `feat/acc-frontend` all have an empty diff against
  trunk under `backend/tests/acceptance/`, so landing this creates no merge debt
  for ACC.

It is an orchestrator **notification** item, not a `HALT`: ACC should know its
file changed. It should not go back to the slice, and it should not block.

**The hard rules:**

| check | result |
|---|---|
| `backend/src/` touched | **0 files** — `git diff trunk...branch -- backend/src` is empty |
| skips / xfails added | **none** — the only `skip` strings in the diff are prose in two docstrings |
| tests deleted | **none** |
| assertions weakened | **none** — the only removed assertion is round 2's `len(after) == 7` → `== 9`, a strengthening |
| assertions removed outright | none; the two removed `return (…)` blocks are the hand-written tuples replaced by `declared_activities(self)` |
| `scripts/ci/known_test_failures.json` | **byte-identical** at merge-base, trunk, branch head and merge tree — blob `2a202f04` |
| `scripts/ci/assert_known_failures.py` | byte-identical trunk ↔ merge tree (`76551783`) |
| `scripts/ci/test_assert_known_failures.py` | byte-identical trunk ↔ merge tree (`dfc04efa`) |
| frozen-module imports added | **none** (`operations/associate_flow`, `agents/order_discovery`, `api/associate_returns`, `api/return_agents`) |
| fact-name string literals added | none |
| `ruff format --check .` / `ruff check .` on merge tree | 1160 formatted / all checks passed |
| `pytest scripts/tests` on merge tree | 4 passed |

**`scripts/ci/suite_size_floor.json` is still correct, and I measured it rather
than reasoned it.** The file is byte-identical trunk ↔ branch ↔ merge tree
(blob `bd2ea045`), and it is a **floor with a 25% ceiling**, not a pin. From the
merge tree's own JUnit report:

    <testcase> elements   5258   (floor: cases 5251)   ->  5258 >= 5251, and below 6563.75
    distinct classnames    441   (floor: files 441)    ->  441 >= 441

`5247 passed + 11 skipped = 5258` reconciles to the element count. The two added
tests live in an existing module, so `files` does not move. The floor needs no
edit, and editing it would be wrong: the file's own rule is that a number is
lowered only in the commit that removes tests, and nothing was removed.

The backend `known_failures` list is **empty** on this trunk, so the run's zero
failures is what the gate requires — there is no allowlisted failure left to be
"repaired" into a red job by this branch.

**Rule 13 — the gate that runs these guards.**
`test_return_case_workflow_replay_compatibility.py` carries no `pytestmark` and
no `live_infra` marker; both guards ran in the default suite above, which is the
suite `checks.yml` invokes. The guard has a gate, and the gate is the one CI
runs.

---

# Correction to the record, and one named follow-up — neither is a finding

**C1 — ledger step:28, Open item 1, states something about coverage that is not
true, and I disproved it by measurement.** The item reads:

> `_GateProbe.all()` is still a hand-written tuple where `declared_activities(self)`
> exists. Deliberately left: ACC's file, ACC's call. **The guard now covers the
> gap either way.**

The last sentence is wrong, or at best equivocates between two different gaps.
The guard reads **declarations** (`declared_activity_names(probe_class)`); the
worker reads **`all()`**. Nothing anywhere asserts the two agree. Injection, on
the merge tree — decorator kept, one `all()` entry removed:

    pytest tests/test_return_case_workflow_replay_compatibility.py
      -> 17 passed

    declared 18 · all() 17 · declared - all(): {'case_has_return_details'}

**The guard is green while the worker under-registers.** That is precisely the
defect this track exists to prevent, and the guard is blind to it in this one
file. The two original probes are immune because they return
`declared_activities(self)`; `_GateProbe` is not.

I am recording this as a **correction, not a finding**, and the distinction is
that no code on this branch is wrong: `all()` and the declarations agree at
18/18 today, measured. What is wrong is a sentence in the ledger, and the
correction now lives in `.plan/reviews/`, which is where the next person
auditing this guard will look.

**R1 — leaving a third hand-written copy: acceptable to merge, but it must be a
named follow-up, not a ledger bullet resting on C1's wrong justification.**
Asked to rule, I rule both halves:

*Acceptable to merge*, because the state is correct today, the file is ACC's,
and the branch has already crossed into it once; a second crossing on this
branch would widen a line HARNESS-2 asked to be kept narrow.

*But it must be registered*, because "ACC's call" is a person and the derived
form exists precisely because a hand-written list rotted **twice** — at
`5b7d60f6` and again the day V1 phase 2's review gate merged. A third copy in
the one file where the guard cannot see it drift is the same bet placed a third
time, and C1 shows the safety net the ledger claims is not there. The change is
one line and one import:

    def all(self) -> tuple[Any, ...]:
        return declared_activities(self)

Registered to the orchestrator for ACC, alongside the step:27 crossing
notification. **Not a finding**: it asks nothing of this branch.

---

# What I ran

Everything reported above was executed by me against `84961b19`, the merge tree
of **current** trunk `16868eaa` with branch head `1f0dda3d`, with `PYTHONPATH`
pinned to that tree's `backend/src` on every Python invocation and the pin
verified to beat the venv's `.pth`. Injections were made in a second archived
copy and every touched file was restored and confirmed byte-identical to the
merge tree before the `ruff` runs.

    full default backend suite   -> 5247 passed, 11 skipped, 514 deselected in 289.92s
    JUnit report                 -> 5258 cases, 441 classnames
    ruff format --check . / ruff check .  -> 1160 formatted / all checks passed
    pytest scripts/tests         -> 4 passed
    injection a (pinned file moved)                    -> caught by the name pin
    injection b (workflow deregistered, floor intact)  -> caught by the floor
    injection c (same, floor lowered to >= 0)          -> caught by the name pin alone
    injection d (activity dropped in a subdirectory)   -> 4 sites reported
    injection d, old importer                          -> ModuleNotFoundError, no finding
    injection e (method renamed, decorator name kept)  -> 17 passed, correctly silent
    injection f (decorator kept, all() entry dropped)  -> 17 passed  [C1]
    _GateProbe: all() == declared, 18/18

**Not run, and not claimed.** The live-infrastructure suite and
`run_real_infra_suite.sh` — both were exercised in HARNESS-2 and neither is
touched by this round's diff (`scripts/dev/run_real_infra_suite.sh` is unchanged
since `2f1c0e50`). The frontend suite. The ledger's wall-clock investigation,
which remains read-and-assessed rather than reproduced, as in round 2.

---

# Summary

The rule I wrote after round 2 was applied to round 3, and it was applied
against a trunk newer than the one the author merged: the merge tree was built
and **run**, not merged and linted. It is green at `5247 passed, 11 skipped,
514 deselected`, the arithmetic against round 2's `2 failed, 5245 passed`
reconciles exactly with skips and deselections unmoved, and the two rows that
changed are the branch's own guard.

F7 was fixed at the layer that matters. The trap was real — the guard reads
declarations, the worker reads a hand-written `all()` — and the branch did not
fall into it: it updated both and I confirmed 18/18 agreement. F5's two nets are
genuinely two, shown by neutralising the floor and watching the name pin answer
alone. F6 extends coverage rather than restoring it, shown by running the same
injection through both importers and watching the old one produce no finding at
all. The failed injection was correctly diagnosed as a wrong injection, and for
a better reason than the one given: it was behaviour-preserving, so a red there
would have been a false positive.

The crossing into ACC's file was handled the way a crossing should be —
declared, separately committed, insert-only, minimal, and along a route the
previous review left open. `backend/src/` is untouched, the allowlist and the CI
scripts are byte-identical, the suite floor is satisfied with the count measured
from the report, and nothing was skipped, xfailed, weakened or deleted to get
here.

One sentence in the ledger claims a coverage the guard does not have, and I have
corrected it with the measurement that disproves it rather than leaving it to be
inherited. The one-line change it points at is ACC's to make and is registered
as a named follow-up.

`PASS`. Merge permitted, as the three-way merge already on the branch.
