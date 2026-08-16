"""Persisted `CaseStatus` -> projected `ReturnCaseStatus`. Explicit, total, and loud.

Two enums describe where a case is and **they are not the same set** (plan
sect. 6.2, hazard D13). `operations/models.py:CaseStatus` is what
`ReturnCaseWorkflow` writes into `cases.status`; `case_projection/vocabulary.py:
ReturnCaseStatus` is the frozen contract the Copilot reads. Neither may be
edited to make the other fit -- the workflow file mirroring `CaseStatus` is
owned elsewhere, and `ReturnCaseStatus` is a frozen contract exported through
OpenAPI. So the reconciliation lives here, as data.

```text
GATHERING_INFO         -> GATHERING_INFO
AWAITING_BAY           -> PROCESSING_RETURN
POLICY_APPROVED        -> PROCESSING_RETURN      (see below)
AWAITING_POLICY_REVIEW -> AWAITING_POLICY_REVIEW
POLICY_REJECTED        -> POLICY_REJECTED
RECOVERY_REQUIRED      -> RECOVERY_REQUIRED
AWAITING_SUPPORT       -> AWAITING_SUPPORT
RMA_RECEIVED           -> PROCESSING_RETURN
IN_TRANSIT             -> PROCESSING_RETURN
CLOSED                 -> POLICY_REJECTED, COMPLETED, or COMPLETED_EXTERNAL_SETTLEMENT
CANCELLED              -> CANCELLED
```

**Nothing has a default.** `_DIRECT` is checked against `CaseStatus` at import,
so adding a member to the persisted enum breaks the import of this module rather
than quietly acquiring a fallback -- a mapping that guesses is a mapping that
will one day report a rejected case as gathering information. A value that is
not a `CaseStatus` at all raises `UnmappedCaseStatusError`.

**`POLICY_APPROVED` is the one compromise and it is deliberate.**
`CaseStatus.POLICY_APPROVED` has no counterpart in `ReturnCaseStatus`; its own
docstring says so. It is transient -- the workflow sets it and immediately opens
the Support work item, which moves the case to `AWAITING_SUPPORT`. Mapping it to
`AWAITING_SUPPORT` would be worse than mapping it to `PROCESSING_RETURN`:
`derive_copilot_stage` treats `AWAITING_SUPPORT` as "Support has this", and the
pane would tell an associate to wait for a queue nobody has been put in yet.
`PROCESSING_RETURN` makes no claim about Support, and a case in it with a policy
evaluation and no RMA derives `POLICY_EVALUATION`, which is exactly where it is.

**`CLOSED` splits three ways**, which is why this is a function and not a
dictionary lookup. `ReturnCaseWorkflow` writes the same `CLOSED` for two
opposite endings -- `_close_business_complete` when the return satisfied its
requirement set, and `_record_support_outcome` when Support *refused* it -- so
the persisted status alone cannot say which happened, and the split is taken on
what else the case records.

**Refusal is read first, and it is not a settlement.** A `CLOSED` case whose
`support_outcome` fact says `REJECTED` is a return somebody examined and
declined; it reaches `POLICY_REJECTED`. Before this, it reached
`COMPLETED_EXTERNAL_SETTLEMENT` -- which asserts a credit settled outside the
platform -- while `awaiting` still named the verification the refusal had
answered. The case was simultaneously finished and waiting, and the finished
half claimed money had changed hands. Neither statement was true, and the
second is the one this programme exists to make impossible: **do not invent a
settlement that did not happen.**

`POLICY_REJECTED` rather than a member of its own, and the trade is stated
rather than hidden. `ReturnCaseStatus` is frozen and shared with the console,
the generated client and four committed OpenAPI documents, so the ten members
are what a projection may say; of the ten, exactly one means *refused on the
merits*, and that is the true ending of a rejected claim. It does cost a
distinction: an evaluator rejection and a Support rejection now share a member.
That distinction survives elsewhere and is recoverable from the same projection
-- an evaluator rejection carries `policyEvaluation.route: STANDARD_RETURN` with
`originalDecision: REJECT` and no work item, while a Support refusal carries a
verification route with `originalDecision: null`, a `support` block, and the
`support_outcome` fact with its reason and its instant. That is the test
`CANCELLED` and `EXPIRED` had to fail before they were split: nothing else told
them apart. Here something does. If the contract is ever reopened, a
`SUPPORT_REJECTED` member is the better answer and this is where it lands.

**Otherwise `CLOSED` splits on settlement.** A completed return whose credit the
platform never produced is `COMPLETED_EXTERNAL_SETTLEMENT`, so that a
completed-return count is never misread as a settled-return count (plan
sect. 13, Phase 9). Settlement has no producer today, so every *completed*
`CLOSED` case reaches the external-settlement member -- correctly, because that
is the true statement about it.

That outcome is **deliberate, not incidental.** `assemble_case_projection_state`
now states `SettlementProjection(status=NOT_INTEGRATED)` on every case rather
than leaving the block absent, so the split is taken on an assertion the
platform made about itself instead of on a hole in the projection. Absence is
still honoured -- `_settlement_is_external` reads it the same way -- because a
hand-built state and the mock server may both omit the block, and the two must
not disagree about a closed case. The difference is that the assembled path no
longer relies on that fallback to reach the right answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from return_platform.operations.case_projection.contract import SettlementProjection
from return_platform.operations.case_projection.vocabulary import (
    ReturnCaseStatus,
    SettlementStatus,
    SupportOutcome,
)
from return_platform.operations.models import CaseStatus

__all__ = [
    "TERMINAL_PERSISTED_STATUSES",
    "UnmappedCaseStatusError",
    "project_case_status",
]


class UnmappedCaseStatusError(ValueError):
    """A persisted status the projection has no reading for.

    Raised rather than defaulted. The alternative -- falling back to
    `GATHERING_INFO`, or to the string itself -- would render a case in a mode
    the platform chose because it did not recognise the truth, and would do it
    silently on every poll.
    """

    def __init__(self, value: object) -> None:
        super().__init__(
            f"case status {value!r} has no projection: add it to "
            "case_projection/status_mapping.py rather than defaulting it"
        )
        self.value = value


#: Terminal in the *persisted* vocabulary. Only `ReturnCaseWorkflow` writes these
#: three, so a case that reached one has finished whatever its execution says.
#:
#: The same set `CaseRepository.list_cases_without_workflow` excludes from
#: recovery, and the set the backfill reads to decide whether an orphan is still
#: active. Declared here because this module is where persisted-status knowledge
#: belongs.
TERMINAL_PERSISTED_STATUSES: Final[frozenset[CaseStatus]] = frozenset(
    {
        CaseStatus.CLOSED,
        CaseStatus.CANCELLED,
        CaseStatus.POLICY_REJECTED,
    }
)


#: Every persisted status whose reading does not depend on anything else.
#: `CLOSED` is absent on purpose -- it is the one that splits.
_DIRECT: Final[Mapping[CaseStatus, ReturnCaseStatus]] = {
    CaseStatus.GATHERING_INFO: ReturnCaseStatus.GATHERING_INFO,
    CaseStatus.AWAITING_BAY: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.POLICY_APPROVED: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.AWAITING_POLICY_REVIEW: ReturnCaseStatus.AWAITING_POLICY_REVIEW,
    CaseStatus.POLICY_REJECTED: ReturnCaseStatus.POLICY_REJECTED,
    CaseStatus.RECOVERY_REQUIRED: ReturnCaseStatus.RECOVERY_REQUIRED,
    CaseStatus.AWAITING_SUPPORT: ReturnCaseStatus.AWAITING_SUPPORT,
    CaseStatus.RMA_RECEIVED: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.IN_TRANSIT: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.CANCELLED: ReturnCaseStatus.CANCELLED,
}

#: The one status whose reading depends on what else the case records: how it
#: closed, and -- when it closed successfully -- whether anybody settled it.
_OUTCOME_DEPENDENT: Final[frozenset[CaseStatus]] = frozenset({CaseStatus.CLOSED})

if set(_DIRECT) | _OUTCOME_DEPENDENT != set(CaseStatus):  # pragma: no cover - import guard
    missing = sorted(status.value for status in set(CaseStatus) - set(_DIRECT))
    raise RuntimeError(
        "every persisted CaseStatus needs a projection: unmapped members would be "
        f"discovered by a 500 on a case read rather than here -- {', '.join(missing)}"
    )


def _settlement_is_external(settlement: SettlementProjection | None) -> bool:
    """Whether the credit is somebody else's to produce.

    Absent counts. There is no settlement producer in the platform today, so a
    case that closed without one settled outside it, and saying `COMPLETED`
    would assert a credit nothing in this system can evidence.
    """
    return settlement is None or settlement.status is SettlementStatus.NOT_INTEGRATED


def project_case_status(
    status: object,
    *,
    settlement: SettlementProjection | None = None,
    support_outcome: SupportOutcome | None = None,
) -> ReturnCaseStatus:
    """Read a persisted status as the contract's status. Never guesses.

    Accepts the enum or the stored string, because Mongo hands back a string and
    a caller that had to coerce first would be the place the coercion went wrong.
    Anything that is not a `CaseStatus` raises `UnmappedCaseStatusError`.

    `support_outcome` is what Support answered, from the `support_outcome` fact
    `record_support_outcome` writes; `None` means Support has not answered, or
    answered before that writer existed. It is read only for `CLOSED`, because
    that is the only persisted status two opposite endings share, and it is read
    **before** settlement: a refused return has no credit to have been settled
    anywhere, so asking the settlement question about it at all would be asking
    which way a payment went that nobody made.
    """
    if isinstance(status, CaseStatus):
        persisted = status
    else:
        if not isinstance(status, str):
            raise UnmappedCaseStatusError(status)
        try:
            persisted = CaseStatus(status)
        except ValueError as error:
            raise UnmappedCaseStatusError(status) from error

    if persisted is CaseStatus.CLOSED:
        if support_outcome is SupportOutcome.REJECTED:
            return ReturnCaseStatus.POLICY_REJECTED
        return (
            ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
            if _settlement_is_external(settlement)
            else ReturnCaseStatus.COMPLETED
        )
    return _DIRECT[persisted]
