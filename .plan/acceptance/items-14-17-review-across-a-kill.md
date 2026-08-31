# Items 14–17 — the review gate across a worker kill, live

**Test:** `backend/tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py`
— 2 scenarios, **live_infra**, both green against the running stack.

## ⚠ THE FINDING THAT CAME FIRST — the live-infra workflow suite is broken

Before writing anything I ran the file whose gap I had named. **12 of its 13
tests fail**:

```
temporalio.exceptions.ApplicationError: NotFoundError: Activity function
record_template_draft ... is not registered on this worker, available activities:
draft_support_request, evaluate_case_eligibility, open_support_work_item,
record_case_customer_identity, record_case_status, record_support_outcome,
request_bay_assignment, resolve_business_deadline, send_support_reminder,
synchronize_return_records
```

`tests/test_return_case_workflow_real_infra.py::_Probe` predates V1's review gate
and never gained the five gate activities. **`workflows/worker.py` registers all
five** (`record_template_draft`, `record_template_revision`,
`rerender_template_draft`, `hold_unsettled_reviews`, `snapshot_sent_template`) —
so **production is correct and the harness is stale**. Every test in that file
that reaches Support has been failing since V1 phase 2 merged.

**And it has happened before.** That file's last commit is
`5b7d60f6 fix(tests): stale workflow doubles wedged the live-infra suite,
silently` (2026-08-23). The same defect, fixed once, recurred — because nothing
runs the suite. This is RV rule 13's sharpest instance on the run: not a guard
without a gate, but a **guard that was repaired for having no gate and then
broke again the same way**.

**Not repaired here.** The file belongs to another slice and ACC owns
`backend/tests/` *additions*. It is reported, with the diagnosis complete enough
to fix in one edit: add the five gate activities to `_Probe` and to `all()`.

**Consequence for the gate's definition of done** — "all 26 items green against
live infra" cannot be asserted from a suite in this state, independently of
anything ACC writes.

## What this module covers, and against what

| plane | real | doubled |
| --- | --- | --- |
| durability | Temporal, `ReturnCaseWorkflow`, worker start/kill/restart | — |
| review | `SupportTemplateGateService` over a **real Mongo database**, review aggregate, approval transition, delivery identity | render inputs (a case projection this file has no reason to build) |
| Channel B | the gate's decision to post | the post itself, counted |

**Item 15 — kill mid-review.** The gate opens, an associate's autosave is
written, the worker is killed, a fresh worker replaces it. After the restart:
the review map and the **remaining timeout** are unchanged (asserted *equal*,
not merely present — "a deadline exists" passes for a gate that restarted its
own clock), the draft row is still `OPEN` at the same `draftVersion`, the
half-typed edit row is still there, and the resumed worker did **not** re-draft.

**Item 14's workflow half** comes with it: `execution_state` — the field a
reloaded panel's countdown is drawn from — is queryable and correct after the
restart. The panel's HTTP composition is not exercised here.

**Item 16 — exactly one message.** The approval is performed by a process that
did not create the draft. One message leaves, under one delivery identity, and
the review reaches `SENT`.

**Item 17's relay half is not covered here** (its omc half is, in
`amendment-4-eventually-once.md`).

## Fault injection — and two of ACC's own scenarios could not fail

| # | injected fault | result |
| --- | --- | --- |
| INJ-15a | `_await_template_reviews` ignores `resumed_template_review_deadline_iso` | **2 passed — MISS**, see below |
| INJ-15b | `execution_state` stops carrying `template_reviews` / `template_review_deadline_iso` | **1 failed, 1 passed** — item 15 reds; item 16 correctly unaffected |
| INJ-16a | the gate's `SENT` short-circuit removed | **MISS twice**, then **1 failed, 1 passed** after the scenario was rebuilt |
| INJ-16b | the workflow's `signal_id` dedupe removed | 2 passed |
| INJ-16c | **both** the above removed together | 2 passed |

### INJ-15a — the miss is a fact about Temporal, and it is recorded rather than papered over

Ignoring the resumed deadline changed nothing, because **a worker kill is not a
`continue_as_new`**: the replacement worker *replays the history*, so the
`resolve_business_deadline` result comes back from history and the resumed-field
path is never taken. That path guards continuation, not restart.

The honest consequence: **for a worker kill, most of item 15's claims hold by
Temporal's replay and are close to unfalsifiable by an edit inside the
workflow.** INJ-15b is what genuinely reds the scenario, and what it proves is
narrower and still worth having: the deployment's actual wiring — activities
registered, gate reachable, review state queryable and correct after the process
is gone. Claiming more would be claiming the framework's guarantee as the
platform's.

### INJ-16a — a scenario that could not fail, twice, in two different costumes

1. **First form:** approve once, count one message. Removing the `SENT`
   short-circuit left it green — nothing ever asked for a second delivery, so a
   count of one is a count of the one send that was requested.
2. **Second form:** signal the approval twice. Still green under INJ-16a, and
   still green under INJ-16b, and **still green under INJ-16c with both guards
   removed together**. Diagnosed rather than guessed: by the time the second
   signal lands the gate has closed and there is no wait to wake, so nothing
   ever reaches the code that decides whether to post again.
3. **Third form:** call `deliver_approved` again directly, putting a genuine
   second delivery in front of the guard that owns the guarantee. INJ-16a then
   **reds**.

Both duplicate paths are kept — the redelivered signal is the ordinary
at-least-once case and its absorption is worth asserting — but only the third
makes "exactly one" a claim.

**Sixth and seventh instrument defects ACC has found in its own work**, both the
same family, and both found only because the injection was run rather than the
green trusted.

### Two latent races, one masking the other

`reached("record_template_draft")` fires when the activity *starts*; the review
id is appended when it *finishes*. Indexing the list straight after passed in
one scenario — a `handle.query` happened to sit between them — and failed in the
other. Removing that race then exposed a second: `template_review_deadline_iso`
is set inside `_await_template_reviews`, *after* the draft activity returns, so
the first scenario had been reading it during a window where `None` is correct.
Both replaced with waits on the thing actually wanted (`first_review_id`,
`_open_gate`).

All injections reverted with `git checkout`; `git status` clean after each. No
production file is modified by this branch.

## The gate that runs this module — nothing in CI

`addopts` deselects `live_infra` and `.github/workflows/checks.yml` runs plain
`pytest tests`. **This module's only gate is a person running
`scripts/dev/run_real_infra_suite.sh`** — which is exactly the condition that
let the stale probe above rot twice. It is marked `live_infra` because a worker
kill needs a worker; the other **34** acceptance tests are in the default suite
deliberately, and that was re-confirmed after the trunk merge (`36 collected,
2 deselected`).

## Suites

* acceptance, live: **2 passed**
* acceptance, default: **34 collected, 0 live-classified**
* full default suite: **5220 passed, 1 failed, 10 skipped, 514 deselected** —
  the allowlisted `test_a_rejected_return_still_opens_no_work_item`. **Zero new
  failures.**


---

## Item 20 — deploy replay across both patch branches, audited

Covered in-slice by `tests/test_return_case_workflow_replay_compatibility.py`
(15 tests) plus the gate suite. Audited rather than duplicated, by flipping the
patch decision each way:

| # | injected fault | result |
| --- | --- | --- |
| INJ-20a | `workflow.patched(_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE)` → `True` — a pre-gate history replays down the gated path | **1 failed** — `test_a_legacy_history_opens_support_instead_of_wedging`, message `unexpected activity record_template_draft` |
| INJ-20b | → `False` — the gated branch is never taken | **19 failed** across the gate suite |

Both branches are genuinely load-bearing and neither is green by accident.
**Item 20: verified.**

## ⚠ A guard that exists, is gated, and does not reach what broke

`test_return_case_workflow_replay_compatibility.py::test_every_activity_the_workflow_calls_is_registered_on_the_worker`
exists **precisely** for the defect found above. Its own docstring:

> An unregistered activity is a stall with no exception anywhere. … Two
> activities added in V3 phase 2 were unregistered against a green suite until
> this existed.

It derives the called set from the workflow's `execute_activity` calls and the
registered set from `worker.py`'s attribute references — correct, gated by the
default suite, and it passes, because **`worker.py` is right**. What it does not
read is the *other* registration surface: the workers the tests themselves
construct. The stale `_Probe` is one, and it is one level outside where the
detector looks.

That is `merge.md`'s *"a detector must reach as far as the thing it protects"*
meeting rule 13: the guard is not missing and is not ungated — its reach stops
short of the surface that rotted, twice.

**The fix has a shape, and it is deliberately not shipped here.** Extending the
same derivation over every `Worker(..., activities=…)` constructed under
`tests/` would catch this class permanently — and it would be **red on arrival**
against the stale probe. A guard that must be born red belongs with the repair,
in one change, owned by the slice that owns the probe. Shipping it alone would
either break the gate or require naming the very defect it exists to catch in an
allowlist, which is the guard excusing its own subject.
