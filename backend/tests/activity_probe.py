"""Derive a probe's activity registrations from the probe itself.

WHY THIS EXISTS
---------------
A test that constructs its own `Worker` has to tell it which activities to
serve. The obvious way to do that is a tuple listing them, and every real-infra
probe in this suite was written that way::

    def all(self):
        return (self.record_case_status, self.request_bay_assignment, ...)

That tuple is a **second copy of a list `worker.py` already owns** -- the set of
activities `ReturnCaseWorkflow` calls -- maintained by hand, in a file nothing
runs by default. It rots the moment the workflow gains an activity, and it rots
*silently*: an unregistered activity raises nothing at registration time. The
worker simply does not poll for it, and the case stops with the task scheduled
and no error anywhere.

It has rotted twice. `5b7d60f6` re-synced it by hand after the same defect; V1
phase 2's review gate added five activities and the tuple went stale again the
day it merged, because the only thing that would have noticed was a script
nobody ran. **A list re-synced by hand rots again** -- which is the argument for
deriving it instead.

So: the probe declares `@activity.defn` methods, and the registration tuple is
computed from those declarations. Adding a method is now sufficient; there is no
second place to update and therefore no second place to forget.

WHAT THIS DOES NOT FIX
----------------------
Deriving the tuple closes the gap between *the methods a probe defines* and
*the methods it registers*. It cannot close the gap between the methods a probe
defines and the activities the workflow calls -- nothing here can, because the
probe's method set is genuinely hand-written.

That second gap is closed by a gate rather than by a mechanism:
`test_every_activity_the_workflow_calls_is_registered_on_the_worker` in
`test_return_case_workflow_replay_compatibility.py`, which reads the workflow's
`execute_activity` calls against every `Worker(..., activities=...)` under
`tests/` and fails in the **default** suite -- the one CI runs -- rather than in
a live-infra run that has to be started by hand.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from temporalio import activity
from temporalio.common import RetryPolicy

from return_platform.workflows.return_case_workflow import (
    _PERSIST_RETRY,
    _PERSIST_TIMEOUT,
)

__all__ = [
    "LIVENESS_CEILING_SECONDS",
    "declared_activities",
    "declared_activity_names",
    "worst_case_activity_seconds",
]


def worst_case_activity_seconds(timeout: timedelta, retry: RetryPolicy) -> float:
    """The wall time one activity may consume while behaving entirely within spec.

    `start_to_close_timeout` is **per attempt**, which is the fact that made
    every wall-clock budget in the live workflow tests assert on a coincidence.
    An attempt that is accepted by a worker and never completed burns the whole
    timeout before the server retries it -- observed, not theorised: a slow run's
    history carries `ACTIVITY_TASK_STARTED` followed 30s later by
    `ACTIVITY_TASK_TIMED_OUT / TIMEOUT_TYPE_START_TO_CLOSE`. So the bound is the
    timeout times the attempt count, plus the backoff between attempts.
    """
    attempts = max(retry.maximum_attempts, 1)
    initial = retry.initial_interval.total_seconds() if retry.initial_interval else 0.0
    coefficient = retry.backoff_coefficient or 1.0
    cap = retry.maximum_interval.total_seconds() if retry.maximum_interval else None

    backoff = 0.0
    for index in range(attempts - 1):
        interval = initial * (coefficient**index)
        backoff += interval if cap is None else min(interval, cap)
    return timeout.total_seconds() * attempts + backoff


#: Headroom over the retry schedule for the rest of the path -- every other
#: activity, the workflow tasks between them, and process startup. Derived from
#: measurement rather than taste: twelve instrumented runs put the *entire*
#: fast path at 2.86-5.29s, so this is roughly three times the worst observed
#: whole-path cost, and it is small beside the term it is added to.
_PATH_MARGIN_SECONDS: Final = 15.0

#: The ceiling for a probe's `reached()` liveness wait.
#:
#: **Derived, uniformly, from the worst retry policy on the path -- not per
#: site.** A per-site bound would be tighter and would offer one more chance per
#: site to under-model exactly one path, which is the error this constant exists
#: to end: the first attempt at it used `_BEST_EFFORT_RETRY` (2 attempts, 61s)
#: because that was the activity observed failing, while `open_support_work_item`
#: sits downstream of `_PERSIST_RETRY` activities at five attempts. One
#: conservative bound cannot be wrong in that direction. Do not "improve" this
#: into fourteen fragile derivations.
#:
#: This is a **liveness net, not a performance assertion.** `reached()` returns
#: as soon as its condition is met, so raising the ceiling costs a passing test
#: nothing and only makes a genuinely stuck one take longer to report.
#:
#: Three call sites deliberately do **not** use it -- see
#: `test_return_case_workflow_real_infra.py`, where a budget below
#: `bay_wait_seconds` *is* the assertion.
LIVENESS_CEILING_SECONDS: Final = (
    worst_case_activity_seconds(_PERSIST_TIMEOUT, _PERSIST_RETRY) + _PATH_MARGIN_SECONDS
)


def _declared(owner: type) -> list[str]:
    """Attribute names on `owner` carrying an `@activity.defn`, MRO included.

    Read off the class rather than an instance so the guard can ask the same
    question of a probe it has no way to construct -- the policy-gate probe
    takes a required argument, and a check that could only see no-argument
    probes would quietly skip the ones it could not build.
    """
    names: list[str] = []
    seen: set[str] = set()
    for klass in owner.__mro__:
        for attribute, member in vars(klass).items():
            if attribute in seen:
                continue
            seen.add(attribute)
            if not callable(member):
                continue
            if activity._Definition.from_callable(member) is not None:
                names.append(attribute)
    return sorted(names)


def declared_activities(probe: Any) -> tuple[Any, ...]:
    """Every `@activity.defn` method on `probe`, bound, ready for `Worker`.

    The registration tuple, derived. Sorted by attribute name so a run's
    registration order does not depend on definition order.
    """
    return tuple(getattr(probe, name) for name in _declared(type(probe)))


def declared_activity_names(owner: type) -> set[str]:
    """The Temporal-visible activity **names** `owner` declares.

    The decorator's `name=` argument, not the Python attribute -- those are the
    names a worker registers under and the names the workflow asks for, and
    they are allowed to differ.
    """
    names: set[str] = set()
    for attribute in _declared(owner):
        definition = activity._Definition.from_callable(getattr(owner, attribute))
        if definition is not None:
            names.add(definition.name)
    return names
