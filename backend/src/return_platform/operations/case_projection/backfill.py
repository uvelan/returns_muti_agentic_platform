"""What an existing case document needs before it projects. Idempotent, never destructive.

Plan sect. 6.7. Eight real cases exist at the baseline and six of them are
orphans -- `AWAITING_SUPPORT` with a `TERMINATED` durable execution -- so the
projection has to be *backward compatible with what is already written* rather
than a cleanup that rewrites it.

The plan names four clauses. **Two of them require no write at all**, and saying
so is the point of this module rather than an omission from it:

```text
missing revision                  -> initialize deterministically   WRITE
missing stage                     -> derive from status + records   read-side
existing record                   -> project into shipments[]       read-side
case active + workflow terminated -> RECOVERY_REQUIRED              WRITE
```

`CopilotStage` is derived and never stored (`vocabulary.py`), so there is no
stage column to fill: `derive_copilot_stage` computes one for every case,
including a case written before the contract existed. Likewise a legacy
`returnRecord` needs nothing done to it -- `assembly.project_shipments` reads its
`trackingReference` and `labelReference` into the plural shapes on every read.
Writing either into Mongo would create a second copy of a derived value, which
is the class of defect this programme is removing.

So this module plans exactly two writes, and both are conditional:

* **`version` absent.** Set it to `0`. `update_case` cannot do it -- its filter
  is `{"version": expected}` and Mongo does not match a missing field against
  `0` -- so the repository writes it directly, guarded by `$exists: false`,
  which is what makes a second run a no-op. `updatedAt` is deliberately *not*
  touched: the projection already reads a missing version as `0`, so nothing an
  associate can see changes, and bumping a timestamp for a change nobody made
  would put every backfilled case at the top of a recency sort.

* **Active case, terminated execution.** Set `status` to `RECOVERY_REQUIRED`
  through `update_case`, which bumps the revision and sets `updatedAt` in the
  same write -- the projection *does* change, so plan sect. 6.5 applies. A
  second run sees the status already recorded, plans nothing and writes nothing.

**Idempotence is a property of the plan, not of the writer.** `plan_case_backfill`
is a pure function of the document, and running it against a document it has
already fixed returns `is_noop`. That is what the idempotence test asserts, and
it cannot be satisfied by a writer that happens to overwrite with equal values.

`RECOVERY_REQUIRED` is **non-terminal** in both enums. Recovery can restart
processing, and marking these six terminal would stop the client polling six
cases that are about to resume.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from return_platform.operations.case_projection.status_mapping import (
    TERMINAL_PERSISTED_STATUSES,
    UnmappedCaseStatusError,
)
from return_platform.operations.models import CaseStatus

__all__ = [
    "CaseBackfillAction",
    "CaseBackfillPlan",
    "plan_case_backfill",
]


class CaseBackfillAction(StrEnum):
    """What the backfill decided to do to one case, and nothing it might have done.

    An enum rather than free text so a migration report can be counted. A run
    that says "6 cases recovered, 0 revisions initialised" is reviewable; a list
    of sentences is not.
    """

    #: The document carried no `version`. Initialised to `0`.
    INITIALIZE_REVISION = "INITIALIZE_REVISION"
    #: Active case, terminated execution. Status set to `RECOVERY_REQUIRED`.
    MARK_RECOVERY_REQUIRED = "MARK_RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class CaseBackfillPlan:
    """The writes one case needs. Empty for a case that is already correct."""

    case_id: str
    #: The version to write against, and the version a missing field is
    #: initialised to. `0` for a document that carries none, which is the value
    #: `INITIALIZE_REVISION` writes -- so the status update that may follow in
    #: the same run has the right expectation without a re-read.
    expected_version: int
    actions: tuple[CaseBackfillAction, ...]
    #: The status to write, or `None`. Separate from `actions` because the
    #: repository needs the value and a reader needs the reason.
    status: CaseStatus | None = None

    @property
    def is_noop(self) -> bool:
        """Whether this case needs nothing. The second run's answer for every case."""
        return not self.actions


def plan_case_backfill(
    case: Mapping[str, Any],
    *,
    workflow_terminated: bool,
) -> CaseBackfillPlan:
    """Decide what one case document needs. Pure, and therefore testable twice.

    `workflow_terminated` is supplied by the caller rather than looked up.
    Nothing in the read path may call Temporal (plan sect. 6.6), and a migration
    that reached the workflow host from inside a projection function would make
    that host a dependency of every test of this rule.

    An unreadable persisted status raises `UnmappedCaseStatusError`. A backfill
    that skipped documents it did not understand would report success over the
    exact cases most likely to need it.
    """
    case_id = str(case.get("caseId") or "").strip()
    if not case_id:
        raise ValueError("a case document with no caseId cannot be backfilled")

    raw_status = case.get("status")
    if isinstance(raw_status, CaseStatus):
        status = raw_status
    elif isinstance(raw_status, str):
        try:
            status = CaseStatus(raw_status)
        except ValueError as error:
            raise UnmappedCaseStatusError(raw_status) from error
    else:
        raise UnmappedCaseStatusError(raw_status)

    actions: list[CaseBackfillAction] = []

    stored_version = case.get("version")
    if isinstance(stored_version, bool) or not isinstance(stored_version, int):
        # Absent, or a value no revision counter can be. Either way the document
        # has no usable revision and one is initialised deterministically.
        version = 0
        actions.append(CaseBackfillAction.INITIALIZE_REVISION)
    else:
        version = stored_version

    recovery: CaseStatus | None = None
    if (
        workflow_terminated
        and status not in TERMINAL_PERSISTED_STATUSES
        and status is not CaseStatus.RECOVERY_REQUIRED
    ):
        recovery = CaseStatus.RECOVERY_REQUIRED
        actions.append(CaseBackfillAction.MARK_RECOVERY_REQUIRED)

    return CaseBackfillPlan(
        case_id=case_id,
        expected_version=version,
        actions=tuple(actions),
        status=recovery,
    )
