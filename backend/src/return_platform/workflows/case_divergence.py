"""Whether a case and its durable execution disagree, and which way. Pure.

Plan sect. 13 Phase 10, and the classification that has to happen *before* any
recovery does. Six real cases read `AWAITING_SUPPORT` with no execution that
will accept a signal; several more cases are legitimately finished and will
attract a late Support event for the rest of their retention. Those two look
identical from the outbox -- a permanently undeliverable command -- and they
are opposites:

```text
execution unexpectedly unavailable + case expected to accept updates
  -> RECOVERY_REQUIRED          restart processing; the event is re-driven

case legitimately terminal + update incompatible with that state
  -> PERMANENTLY_REJECTED       the case stays terminal; the event is retained
```

**The distinction is not "did delivery fail".** It is whether the case reached
a terminal state legitimately, or the execution vanished under a case that was
still expecting work. Only the second is recovery, and the plan's forbidden
list names the confusion directly: *resurrect a legitimately terminal case from
a late event*.

Terminality is read from the **persisted** status and nothing else, exactly as
`isTerminal` is (`case_projection/vocabulary.py`). The read path never calls
Temporal, and neither does this module -- `execution` is supplied by the caller,
so every rule here is testable without a workflow host. That mirrors
`plan_case_backfill`, which takes `workflow_terminated` for the same reason and
which this module deliberately does **not** re-implement: the backfill owns the
*write* that marks an orphan, and this owns the *reading* that a route, a
reconciler and an operator all need to share.

`RECOVERY_REQUIRED` is not terminal in either enum. Recovery can restart
processing, and marking it terminal would stop the Copilot polling a case that
is about to resume.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from return_platform.operations.case_projection.contract import SettlementProjection
from return_platform.operations.case_projection.status_mapping import (
    TERMINAL_PERSISTED_STATUSES,
    UnmappedCaseStatusError,
    project_case_status,
)
from return_platform.operations.case_projection.vocabulary import ReturnCaseStatus
from return_platform.operations.models import CaseStatus

__all__ = [
    "EXECUTION_ACCEPTS_UPDATES",
    "EXECUTION_UNAVAILABLE",
    "CaseDivergence",
    "CaseDivergenceAssessment",
    "CaseExecutionState",
    "DivergenceReason",
    "LateEventDisposition",
    "classify_case_divergence",
    "read_persisted_status",
]


class CaseExecutionState(StrEnum):
    """What the workflow host says about the execution that owns a case.

    Four members, and the fourth is the important one. `UNKNOWN` is not a
    failure to look -- it is the honest answer when the host could not be
    reached, and it is what stops a Temporal outage from being read as "every
    case is an orphan" and restarting the whole estate.

    `CLOSED` collapses `COMPLETED`, `FAILED`, `TERMINATED`, `CANCELED` and
    `TIMED_OUT` on purpose. They differ in why the execution ended and not at
    all in what this module decides: none of them will accept a signal, and a
    classification that branched on which of them it was would be inventing a
    distinction the recovery rule does not have. The workflow host's own status
    name travels separately, on `CaseDivergenceAssessment.execution_detail`, for
    the operator who does want to know.

    `ABSENT` is separate from `CLOSED` because it is a different fact: no
    execution with this id exists at all -- never started, or aged out of
    retention. Both mean the case is unreachable; only `ABSENT` means there is
    nothing left to read.
    """

    RUNNING = "RUNNING"
    CLOSED = "CLOSED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


#: The one state in which a signal can still land.
EXECUTION_ACCEPTS_UPDATES: Final[frozenset[CaseExecutionState]] = frozenset(
    {CaseExecutionState.RUNNING}
)

#: The states that mean "nothing will accept an update for this case". Note that
#: `UNKNOWN` is in neither set: it is not evidence of either.
EXECUTION_UNAVAILABLE: Final[frozenset[CaseExecutionState]] = frozenset(
    {CaseExecutionState.CLOSED, CaseExecutionState.ABSENT}
)


class CaseDivergence(StrEnum):
    """The reading of one case against its execution."""

    #: Active case, live execution. Nothing is owed.
    HEALTHY = "HEALTHY"
    #: Active case, no execution that will accept an update. The orphan.
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    #: The case finished legitimately. Never recovered, whatever arrives late.
    CASE_TERMINAL = "CASE_TERMINAL"
    #: The workflow host could not be asked. Deliberately its own member rather
    #: than a pessimistic `RECOVERY_REQUIRED`: an unreachable Temporal would
    #: otherwise classify every case as an orphan at once, and the recovery of
    #: that mistake is far more expensive than waiting for the next pass.
    INDETERMINATE = "INDETERMINATE"


class DivergenceReason(StrEnum):
    """*Why* the reading came out that way, in the words an operator needs.

    `CaseDivergence` answers "what do we do"; this answers "what is wrong", and
    the two are separate because one divergence has several causes and an
    operator staring at a stuck case needs the cause. `RECOVERY_REQUIRED` from a
    terminated execution and `RECOVERY_REQUIRED` from a case whose workflow was
    never started are the same action and completely different incidents.
    """

    #: Active case, live execution.
    EXECUTION_LIVE = "EXECUTION_LIVE"
    #: Active case, execution ended without the case reaching a terminal status.
    EXECUTION_CLOSED_UNDER_ACTIVE_CASE = "EXECUTION_CLOSED_UNDER_ACTIVE_CASE"
    #: Active case, no execution with this id exists.
    EXECUTION_MISSING_UNDER_ACTIVE_CASE = "EXECUTION_MISSING_UNDER_ACTIVE_CASE"
    #: The case is terminal and its execution has ended. The ordinary end state.
    TERMINAL_CASE_SETTLED = "TERMINAL_CASE_SETTLED"
    #: The case is terminal and an execution is somehow still open. Divergence,
    #: but not this direction's: a terminal case is never restarted, so this is
    #: reported and left alone rather than "recovered".
    TERMINAL_CASE_WITH_OPEN_EXECUTION = "TERMINAL_CASE_WITH_OPEN_EXECUTION"
    #: The workflow host could not be asked.
    EXECUTION_STATE_UNKNOWN = "EXECUTION_STATE_UNKNOWN"


class LateEventDisposition(StrEnum):
    """What to do with a durable event whose delivery has permanently failed.

    The outbox already knows the delivery cannot succeed as written; that is
    what `REQUIRES_RECONCILIATION` records. This says what the *business* answer
    is, which the outbox has no way to know.
    """

    #: The execution is live after all -- the command was dead-lettered against
    #: a Temporal blip that has since cleared, or recovery has already restarted
    #: the case. Redeliver it; do not ask Support to re-send.
    DELIVERABLE = "DELIVERABLE"
    #: Active case, no execution. Recover the case, then redeliver.
    DRIVES_RECOVERY = "DRIVES_RECOVERY"
    #: The case is legitimately terminal. The event is retained for audit and is
    #: never applied. This is the one the plan's forbidden list is about.
    PERMANENTLY_REJECTED = "PERMANENTLY_REJECTED"
    #: Cannot be decided right now. Left exactly as found, for the next pass.
    INDETERMINATE = "INDETERMINATE"


_DISPOSITION: Final[Mapping[CaseDivergence, LateEventDisposition]] = {
    CaseDivergence.HEALTHY: LateEventDisposition.DELIVERABLE,
    CaseDivergence.RECOVERY_REQUIRED: LateEventDisposition.DRIVES_RECOVERY,
    CaseDivergence.CASE_TERMINAL: LateEventDisposition.PERMANENTLY_REJECTED,
    CaseDivergence.INDETERMINATE: LateEventDisposition.INDETERMINATE,
}

if set(_DISPOSITION) != set(CaseDivergence):  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every CaseDivergence needs a late-event disposition: an unmapped member "
        "would decide a Support event's fate by KeyError"
    )


@dataclass(frozen=True, slots=True)
class CaseDivergenceAssessment:
    """One case, read against its execution. Everything a caller needs, no IO.

    Carries both statuses because they answer different questions.
    `persisted_status` is what `ReturnCaseWorkflow` wrote and what the terminal
    decision is taken on; `projected_status` is what the Copilot and the
    operator see, and `COMPLETED_EXTERNAL_SETTLEMENT` and `CANCELLED` are only
    distinguishable there.
    """

    case_id: str
    persisted_status: CaseStatus
    projected_status: ReturnCaseStatus
    execution: CaseExecutionState
    divergence: CaseDivergence
    reason: DivergenceReason
    #: The workflow host's own word for the execution state, when the caller had
    #: one to pass. Present so an operator can tell a `TERMINATED` orphan from a
    #: `TIMED_OUT` one without this module having to care about the difference,
    #: and it is presentation only -- no rule here reads it.
    execution_detail: str | None = None

    @property
    def is_recoverable(self) -> bool:
        """Whether restarting this case's execution is the correct repair.

        The single predicate every caller gates on, so "recoverable" cannot come
        to mean one thing in the sweep and another on the route.
        """
        return self.divergence is CaseDivergence.RECOVERY_REQUIRED

    @property
    def late_event(self) -> LateEventDisposition:
        """What a durable, permanently undeliverable event against this case is owed."""
        return _DISPOSITION[self.divergence]


def read_persisted_status(value: object) -> CaseStatus:
    """The stored status as an enum, or a refusal. Never a guess.

    Mongo hands back a string, and `UnmappedCaseStatusError` is raised for
    anything that is not a member -- the same refusal `project_case_status`
    makes, for the same reason. A classifier that defaulted an unreadable status
    would decide "not terminal" for a value it did not recognise, and the first
    thing it would do with that decision is restart a finished case.
    """
    if isinstance(value, CaseStatus):
        return value
    if not isinstance(value, str):
        raise UnmappedCaseStatusError(value)
    try:
        return CaseStatus(value)
    except ValueError as error:
        raise UnmappedCaseStatusError(value) from error


def classify_case_divergence(
    case: Mapping[str, Any],
    *,
    execution: CaseExecutionState,
    execution_detail: str | None = None,
    settlement: SettlementProjection | None = None,
) -> CaseDivergenceAssessment:
    """Read one case document against the state of its execution.

    Terminality first, and that ordering is the whole safety property. A
    legitimately terminal case is never recovered no matter what its execution
    looks like -- including the `UNKNOWN` a Temporal outage produces, which is
    why the terminal branch is taken before the outage branch rather than after
    it. The alternative ordering would leave a window in which an outage could
    make a completed case look indeterminate and then, on the pass after,
    recoverable.

    `settlement` only affects `projected_status`: it is what splits a persisted
    `CLOSED` into `COMPLETED` and `COMPLETED_EXTERNAL_SETTLEMENT`. Both are
    terminal, so it can never change the decision -- it changes only the word
    the operator is shown.
    """
    case_id = str(case.get("caseId") or "").strip()
    if not case_id:
        raise ValueError("a case document with no caseId cannot be classified")

    persisted = read_persisted_status(case.get("status"))
    projected = project_case_status(persisted, settlement=settlement)

    def assessed(divergence: CaseDivergence, reason: DivergenceReason) -> CaseDivergenceAssessment:
        return CaseDivergenceAssessment(
            case_id=case_id,
            persisted_status=persisted,
            projected_status=projected,
            execution=execution,
            divergence=divergence,
            reason=reason,
            execution_detail=execution_detail or None,
        )

    if persisted in TERMINAL_PERSISTED_STATUSES:
        return assessed(
            CaseDivergence.CASE_TERMINAL,
            (
                DivergenceReason.TERMINAL_CASE_WITH_OPEN_EXECUTION
                if execution in EXECUTION_ACCEPTS_UPDATES
                else DivergenceReason.TERMINAL_CASE_SETTLED
            ),
        )
    if execution is CaseExecutionState.UNKNOWN:
        return assessed(CaseDivergence.INDETERMINATE, DivergenceReason.EXECUTION_STATE_UNKNOWN)
    if execution in EXECUTION_ACCEPTS_UPDATES:
        return assessed(CaseDivergence.HEALTHY, DivergenceReason.EXECUTION_LIVE)
    return assessed(
        CaseDivergence.RECOVERY_REQUIRED,
        (
            DivergenceReason.EXECUTION_MISSING_UNDER_ACTIVE_CASE
            if execution is CaseExecutionState.ABSENT
            else DivergenceReason.EXECUTION_CLOSED_UNDER_ACTIVE_CASE
        ),
    )
