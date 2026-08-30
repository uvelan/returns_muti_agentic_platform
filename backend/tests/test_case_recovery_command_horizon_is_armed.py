"""V1 carry-forward condition 3: the command-horizon rule, actually wired.

S2 built the rule and proved it by construction -- every behavioural test in
`test_case_recovery_review_waits.py` passes `command_horizon=` by hand. What no
test asked was whether *production* passes one, and it did not: the only two
processes that build a recovery service go through
`build_case_recovery_service`, which supplied `outbox=` and nothing else, so
`_stale_commands` returned `()` for every case in the estate and the rule was
inert. S2 recorded that honestly (the outcome said `RELAUNCHED` rather than
pretending to have checked) and handed the arming to V1.

So this file deliberately does **not** re-test the rule. It tests the wiring,
because the wiring is the part that was missing, and it tests it end-to-end
through the shipped factory with a real `MongoReconciliationOutbox` over a real
outbox collection -- not by reading a private attribute, which would still pass
if the port were wired to something that never answers.

The fault-injection check that makes this a guard rather than an assertion:
delete `command_horizon=reconciliation` from the factory and
`test_a_stale_command_reaches_the_factory_built_service` fails with
`RELAUNCHED != OPERATIONS_REQUIRED`, and the case is relaunched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from temporalio.client import WorkflowExecutionStatus

from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.operations.integrations.outbox import INTEGRATION_OUTBOX_COLLECTION
from return_platform.operations.models import CaseStatus
from return_platform.workflows.return_case_recovery import (
    MongoReconciliationOutbox,
    RecoveryAction,
    build_case_recovery_service,
)
from tests.operations.mongo_double import FakeClient

_async = pytest.mark.asyncio

CASE_ID = "case-horizon-1"
DATABASE = "return_platform_test"


class _ClosedExecution:
    """`describe()` on an execution that is no longer running.

    The real `WorkflowExecutionStatus`, not a string: the probe compares it by
    identity against `RUNNING` and then reads `.name`, so a stringly-typed
    double would take the same branch for the wrong reason -- and would keep
    passing if the probe's mapping changed underneath it.
    """

    status = WorkflowExecutionStatus.COMPLETED


class _Handle:
    async def describe(self) -> _ClosedExecution:
        return _ClosedExecution()


class _Temporal:
    """The one method both the probe and the launcher factory reach for."""

    def __init__(self) -> None:
        self.started: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        del workflow_id
        return _Handle()

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:
        # Recorded and allowed to succeed. A double that raised here would make
        # "did not relaunch" true for the wrong reason -- the service swallows a
        # launcher failure into `RELAUNCH_FAILED`, which is not the same answer
        # as `OPERATIONS_REQUIRED` and must not be able to stand in for it.
        del args
        self.started.append(str(kwargs.get("id", "")))
        return None


class _Repository:
    """The three methods `build_case_recovery_service` hands to the service."""

    def __init__(self, case: dict[str, Any]) -> None:
        self._case = case
        self.backfilled: list[str] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return dict(self._case) if case_id == self._case["caseId"] else None

    async def backfill_case(self, case_id: str, *, workflow_terminated: bool) -> None:
        del workflow_terminated
        self.backfilled.append(case_id)

    async def bind_case_workflow(self, case_id: str, *, workflow_id: str) -> bool:
        del case_id, workflow_id
        return True

    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int
    ) -> list[dict[str, Any]]:
        del created_before, limit
        return []


def _case(status: CaseStatus = CaseStatus.AWAITING_TEMPLATE_REVIEW) -> dict[str, Any]:
    return {
        "caseId": CASE_ID,
        "tenantId": "default",
        "principalId": "dev-operator",
        "status": status.value,
        "channelAConversationId": "disc-1",
        "workflowId": None,
        "configurationReleaseId": "release-1",
        "version": 3,
        "createdAt": datetime(2020, 1, 1, tzinfo=UTC),
        "updatedAt": datetime(2020, 1, 1, tzinfo=UTC),
    }


def _stale_command(case_id: str = CASE_ID) -> dict[str, Any]:
    """One command that committed, never reached the workflow, and aged out.

    `status` is `RETRY`, not `DEAD_LETTER`, on purpose: that is the population
    `list_unapplied_commands_past_horizon` exists for and the dead-letter sweep
    never sees. A fixture that used `DEAD_LETTER` would pass against a horizon
    port wired to `list_commands_requiring_reconciliation` by mistake.
    """
    return {
        "_id": "cmd-stale-1",
        "topic": "return-case.command.signal",
        "aggregateId": case_id,
        "payload": {"caseId": case_id, "review_id": "rev-1"},
        "status": "RETRY",
        "attemptCount": 9,
        "createdAt": datetime.now(UTC) - timedelta(days=2),
    }


async def _service(mongo: FakeClient, case: dict[str, Any], temporal: _Temporal) -> Any:
    return build_case_recovery_service(
        temporal=temporal,  # type: ignore[arg-type]
        repository=_Repository(case),
        database=mongo[DATABASE],  # type: ignore[arg-type]
        timings=ReturnCaseTimingConfiguration(),
        task_queue="return-platform-return-v1",
    )


# --------------------------------------------------------------------------- #
# The wiring
# --------------------------------------------------------------------------- #


@_async
async def test_a_stale_command_reaches_the_factory_built_service() -> None:
    """The condition-3 guard, end to end through the shipped factory.

    Nothing here passes a horizon port. The command is written to the real
    outbox collection, the service is built the way both production processes
    build it, and the decision is read off the outcome. Unwire the factory and
    this is `RELAUNCHED`.
    """
    mongo = FakeClient()
    await mongo[DATABASE][INTEGRATION_OUTBOX_COLLECTION].insert_one(_stale_command())
    temporal = _Temporal()

    service = await _service(mongo, _case(), temporal)
    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.OPERATIONS_REQUIRED
    assert outcome.stale_command_ids == ("cmd-stale-1",)
    assert temporal.started == []


@_async
async def test_with_no_stale_command_the_factory_built_service_still_relaunches() -> None:
    """The other half, and the reason the first test is not vacuous.

    A horizon wired to something that answered "stale" unconditionally would
    pass the test above and break every genuine recovery. Same service, same
    factory, empty outbox: the case is relaunched.
    """
    mongo = FakeClient()
    temporal = _Temporal()
    service = await _service(mongo, _case(), temporal)

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.stale_command_ids == ()
    assert temporal.started == [f"return-case-{CASE_ID}"]


@_async
async def test_the_horizon_reads_the_same_queue_the_outbox_does() -> None:
    """One collection, one reader.

    The factory builds a single `MongoReconciliationOutbox` and passes it as
    both ports. Two objects would be two readers of one queue, and the failure
    that produces -- a horizon over a different database than the dead-letter
    sweep -- is invisible until an incident.
    """
    mongo = FakeClient()
    temporal = _Temporal()
    service = await _service(mongo, _case(), temporal)

    outbox = service._outbox  # noqa: SLF001 - identity is the assertion
    horizon = service._command_horizon  # noqa: SLF001
    assert isinstance(outbox, MongoReconciliationOutbox)
    assert horizon is outbox


@_async
async def test_a_deployment_with_no_database_still_builds_and_still_degrades() -> None:
    """`database=None` is a supported call (the operator read surface makes it).

    Both ports go `None` together, which is the honest state: no queue to ask,
    so no answer claimed. What must not happen is a crash at construction, and
    what must not happen is a *fabricated* `OPERATIONS_REQUIRED`.
    """
    temporal = _Temporal()
    service = build_case_recovery_service(
        temporal=temporal,  # type: ignore[arg-type]
        repository=_Repository(_case()),
        database=None,
        timings=ReturnCaseTimingConfiguration(),
        task_queue="return-platform-return-v1",
    )

    assert service._outbox is None  # noqa: SLF001
    assert service._command_horizon is None  # noqa: SLF001

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.stale_command_ids == ()
