"""The policy gate inside `ReturnCaseWorkflow.run` (3A.7).

**A rejected return cannot reach Support**, and that claim is asserted against
the *work-item collection* -- the store `open_case_thread` writes into -- rather
than against a status, a view, or a call log. A case can be marked
`POLICY_REJECTED` and still have opened a thread with a human on the other end
of it; only the collection settles that.

WHY THIS RUNS THE WORKFLOW WITHOUT TEMPORAL
-------------------------------------------
`ReturnCaseWorkflow` is an ordinary class. `@workflow.defn` and
`@workflow.signal` attach metadata; the run loop is a coroutine that reaches the
outside world through exactly six functions on `temporalio.workflow` --
`execute_activity`, `now`, `uuid4`, `wait_condition`, `info` and `logger`. The
harness below substitutes those six and drives the loop directly, with the
**real** `ReturnCaseActivities`, the **real** deterministic evaluator and the
**real** Ferguson rule set from `config/returns/production.yaml` behind them.

What that buys: the branching, the ordering and the fact writes are the shipped
ones, and the test runs in the normal suite where a regression in the gate is
seen on every commit rather than only where a Temporal server happens to be up.

What it deliberately does not claim: durability. A timer that survives a worker
restart, a signal delivered to a replayed execution, `continue_as_new` -- none of
that is provable here, and `tests/test_return_case_policy_gate_real_infra.py`
proves it against a real server instead. The two files are the same scenarios at
two different levels, on purpose: the durability one cannot run in the default
suite, and the gate is too important to be checked only where infrastructure is.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.policy import PolicyRule, UnstatedConditionFacts
from return_platform.workflows import return_case_activities as activities_module
from return_platform.workflows import return_case_workflow as workflow_module
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import (
    PolicyDecisionName,
    PolicyGateState,
    PolicyOverrideNotice,
    PolicyRouteName,
    ReturnCaseOutcome,
    ReturnCaseStatus,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
)

pytestmark = pytest.mark.asyncio

CASE_ID = "case-under-test"
#: The instant every evaluation is made at. Fixed, so a 30-day window boundary
#: is a value the test states rather than one the calendar decides today.
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
CONFIGURATION_PATH = Path("config/returns/production.yaml")


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Repository:
    """The case, its facts, and nothing else this gate touches.

    `latest_case_facts` reimplements `CaseRepository`'s projection rule --
    newest per name, ties broken on `factId` -- because the assembly under test
    depends on it and a double that resolved ties differently would test a
    projection nobody runs.
    """

    def __init__(self, facts: list[dict[str, Any]] | None = None) -> None:
        self.case: dict[str, Any] = {
            "caseId": CASE_ID,
            "tenantId": "tenant-a",
            "principalId": "associate-1",
            "status": ReturnCaseStatus.GATHERING_INFO.value,
            "channelBWorkItemId": None,
            "orderReference": "CW273354",
            "version": 0,
        }
        self.facts: list[dict[str, Any]] = list(facts or [])
        self.statuses: list[str] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.case if case_id == CASE_ID else None

    async def update_case(
        self, case_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        assert expected_version == self.case["version"], "the activity lost a version race"
        self.case.update(updates)
        self.case["version"] = expected_version + 1
        if "status" in updates:
            self.statuses.append(str(updates["status"]))
        return self.case

    async def append_case_fact(self, **fact: Any) -> dict[str, Any]:
        if any(existing["factId"] == fact["fact_id"] for existing in self.facts):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("factId")
        document = {
            "factId": fact["fact_id"],
            "caseId": fact["case_id"],
            "factName": fact["fact_name"],
            "value": fact["value"],
            "acquisitionMethod": fact["acquisition_method"].value,
            "sourceSystem": fact.get("source_system"),
            "sourcePath": fact.get("source_path"),
            "supersedesFactId": fact.get("supersedes_fact_id"),
            "observedAt": fact.get("observed_at") or NOW,
            "recordedAt": NOW + timedelta(seconds=len(self.facts)),
        }
        self.facts.append(document)
        return document

    async def list_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts if fact["caseId"] == case_id]

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for document in await self.list_case_facts(case_id):
            name = str(document["factName"])
            current = latest.get(name)
            if current is None or (document["recordedAt"], str(document["factId"])) >= (
                current["recordedAt"],
                str(current["factId"]),
            ):
                latest[name] = document
        return latest

    def value_of(self, name: str) -> Any:
        for document in reversed(self.facts):
            if document["factName"] == name:
                return document["value"]
        return None


class _SupportService:
    """The work-item collection, as small as the gate needs it.

    `work_items` is the assertion surface for the central claim of this phase.
    Nothing here filters or hides: a thread opened is a row in the dictionary,
    which is what "assert on the work-item collection" means when the collection
    is not a real Mongo one.
    """

    def __init__(self) -> None:
        self.work_items: dict[str, dict[str, Any]] = {}
        self.reminders: list[str] = []

    async def open_case_thread(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        queue: str | None = None,
        sla_due_at: datetime | None = None,
    ) -> str:
        existing = next(
            (item for item in self.work_items.values() if item["caseId"] == case_id), None
        )
        if existing is not None:
            return str(existing["id"])
        work_item_id = f"wi-{len(self.work_items) + 1}"
        self.work_items[work_item_id] = {
            "id": work_item_id,
            "caseId": case_id,
            "tenantId": tenant_id,
            "queue": queue or "RETURNS_SUPPORT",
            "draft": support_draft,
            "idempotencyKey": idempotency_key,
            "principalId": principal_id,
            # Recorded exactly as supplied, `None` included. `None` is the
            # statement "no deadline but the desk's own", which is what the real
            # service turns into `now + sla_minutes`; a double that filled it in
            # here would hide the difference this file exists to assert.
            "slaDueAt": sla_due_at,
        }
        return work_item_id

    async def post_reminder(self, **kwargs: Any) -> None:
        self.reminders.append(str(kwargs.get("idempotency_key")))


class _SupportServiceWithoutSla(_SupportService):
    """A support service from before the deadline argument existed.

    Kept because `open_support_work_item` inspects the signature rather than
    assuming it, and the branch that copes with an older service is otherwise
    unreachable from any test.
    """

    async def open_case_thread(  # type: ignore[override]
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        queue: str | None = None,
    ) -> str:
        return await super().open_case_thread(
            case_id=case_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            support_draft=support_draft,
            idempotency_key=idempotency_key,
            queue=queue,
        )


class _ContinueAsNew(Exception):
    """Raised by the harness where Temporal would reset history."""

    def __init__(self, workflow_input: ReturnCaseWorkflowInput) -> None:
        super().__init__("continue_as_new")
        self.workflow_input = workflow_input


class _Info:
    @staticmethod
    def is_continue_as_new_suggested() -> bool:
        return False


class _Runtime:
    """The six `temporalio.workflow` functions the run loop actually calls.

    `wait_condition` is the interesting one. A real Temporal timer either fires
    or is cut short by a signal; here, each call first runs the next scheduled
    arrival (a signal landing mid-wait), then answers the predicate, and
    otherwise advances the clock by the timeout and raises `TimeoutError` --
    which is exactly what the workflow's own `except TimeoutError` expects.
    Advancing the clock is what makes a bounded wait terminate instead of
    spinning.
    """

    def __init__(
        self,
        activities: dict[str, Callable[[Any], Awaitable[Any]]],
        *,
        arrivals: list[Callable[[], None]] | None = None,
    ) -> None:
        self._activities = activities
        self._arrivals = list(arrivals or [])
        self._uuid = 0
        self.calls: list[str] = []
        self.instant = NOW
        self.logger = logging.getLogger("tests.policy.gate")

    async def execute_activity(self, name: str, argument: Any, **_options: Any) -> Any:
        self.calls.append(name)
        return await self._activities[name](argument)

    def now(self) -> datetime:
        return self.instant

    def uuid4(self) -> uuid.UUID:
        self._uuid += 1
        return uuid.UUID(int=self._uuid)

    async def wait_condition(
        self,
        predicate: Callable[[], bool],
        *,
        # ASYNC109: the signature mirrors `workflow.wait_condition`, which is
        # what the workflow calls. A different one would not be a substitute.
        timeout: timedelta | None = None,  # noqa: ASYNC109
        timeout_summary: str | None = None,
    ) -> None:
        del timeout_summary
        if self._arrivals:
            self._arrivals.pop(0)()
        if predicate():
            return
        self.instant += timeout if timeout is not None else timedelta(seconds=1)
        raise TimeoutError

    @staticmethod
    def info() -> _Info:
        return _Info()

    @staticmethod
    def all_handlers_finished() -> bool:
        return True

    @staticmethod
    def continue_as_new(workflow_input: ReturnCaseWorkflowInput) -> None:
        raise _ContinueAsNew(workflow_input)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def configuration() -> ReturnPlatformConfiguration:
    """The shipped release, policy and all.

    Loaded rather than hand-built: the claim "the Ferguson rule set is live on
    the case path" is only worth something if the rule set under test is the one
    a container would activate.
    """
    return load_return_configuration(CONFIGURATION_PATH).configuration


@pytest.fixture(scope="module")
def reviewing_configuration(
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """The shipped release with the condition checks switched back on.

    The shipped release sets `unstated_condition_facts: NOT_EVALUATED` -- the
    operator's 2026-08-16 narrowing, which makes the return window the
    determinative question and lets a fact nobody stated stop objecting. That is
    the right default for this deployment and the wrong fixture for a test about
    what happens *after* a case reaches review, because under it a case with an
    unstated fact does not reach review at all.

    So the tests below that need a reviewing case ask for one explicitly, by
    flipping the one value a deployment flips. Doing it here rather than by
    hand-building a policy keeps them running against the shipped rule set,
    which is the point of the module-level fixture above.
    """
    policy = configuration.return_eligibility_policy
    assert policy is not None
    return configuration.model_copy(
        update={
            "return_eligibility_policy": policy.model_copy(
                update={
                    "standard_stock_return": policy.standard_stock_return.model_copy(
                        update={
                            "unstated_condition_facts": UnstatedConditionFacts.REVIEW_REQUIRED
                        }
                    )
                }
            )
        }
    )


def _timings(**overrides: Any) -> ReturnCaseTimings:
    base: dict[str, Any] = {
        # Zero, so the bay step resolves from its activity and waits for nothing.
        # Bay is not what this file is about.
        "bay_wait_seconds": 0,
        "support_response_wait_seconds": 300,
        "reminder_interval_seconds": 60,
        "max_reminders": 1,
        "on_reminders_exhausted": "PARK_FOR_OPERATIONS",
        "business_calendar_id": "default",
        "timezone": "UTC",
    }
    base.update(overrides)
    return ReturnCaseTimings(**base)


def _fact(
    name: str,
    value: Any,
    *,
    acquisition: str = "STATED",
    fact_id: str | None = None,
    recorded_at: datetime | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    return {
        "factId": fact_id or f"fact-{name}",
        "caseId": CASE_ID,
        "factName": name,
        "value": value,
        "acquisitionMethod": acquisition,
        "sourceSystem": "CONVERSATION",
        "sourcePath": "CONVERSATION_MESSAGE",
        "supersedesFactId": supersedes,
        "observedAt": recorded_at or NOW - timedelta(hours=1),
        "recordedAt": recorded_at or NOW - timedelta(hours=1),
    }


#: Everything the standard stocked path needs to approve. Each fact is stated,
#: so every one of them is admissible evidence; tests remove exactly one at a
#: time to show what its absence does.
def _approvable_facts() -> list[dict[str, Any]]:
    stated_true = (
        "condition_new",
        "suitable_for_resale",
        "original_packaging",
        "packaging_undamaged",
        "all_original_parts",
        "seller_stocked",
    )
    stated_false = (
        "used",
        "installed",
        "modified",
        "rebuilt",
        "reconditioned",
        "repaired",
        "altered",
        "damaged",
        "special_order",
        "non_stock",
    )
    return [
        *(_fact(name, True) for name in stated_true),
        *(_fact(name, False) for name in stated_false),
        _fact("return_reason", "CHANGED_MIND"),
        _fact("purchase_date", "2026-08-01T10:00:00+00:00"),
    ]


class _Harness:
    def __init__(
        self, repository: _Repository, support: _SupportService, runtime: _Runtime
    ) -> None:
        self.repository = repository
        self.support = support
        self.runtime = runtime


async def _run_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    configuration: ReturnPlatformConfiguration | None,
    facts: list[dict[str, Any]] | None = None,
    timings: ReturnCaseTimings | None = None,
    arrivals: list[Callable[[], None]] | None = None,
    workflow_input: ReturnCaseWorkflowInput | None = None,
    support: _SupportService | None = None,
) -> tuple[ReturnCaseOutcome, _Harness]:
    """Drive one whole `run` with the real activities behind it."""
    repository = _Repository(facts)
    support = support if support is not None else _SupportService()
    activities = ReturnCaseActivities(
        repository=repository,  # type: ignore[arg-type]
        support_service=support,
        configuration=lambda: configuration,
    )
    table: dict[str, Callable[[Any], Awaitable[Any]]] = {
        "record_case_status": activities.record_case_status,
        "resolve_business_deadline": activities.resolve_business_deadline,
        "request_bay_assignment": activities.request_bay_assignment,
        "evaluate_case_eligibility": activities.evaluate_case_eligibility,
        "draft_support_request": activities.draft_support_request,
        "open_support_work_item": activities.open_support_work_item,
        "send_support_reminder": activities.send_support_reminder,
    }
    runtime = _Runtime(table, arrivals=arrivals)
    monkeypatch.setattr(workflow_module, "workflow", runtime)

    instance = ReturnCaseWorkflow()
    harness = _Harness(repository, support, runtime)
    outcome = await instance.run(
        workflow_input
        or ReturnCaseWorkflowInput(
            case_id=CASE_ID,
            tenant_id="tenant-a",
            principal_id="associate-1",
            conversation_id="conv-1",
            configuration_release_id="release-1",
            timings=timings or _timings(),
        )
    )
    return outcome, harness


# ---------------------------------------------------------------------------
# The central claim
# ---------------------------------------------------------------------------


async def test_an_out_of_policy_return_is_rejected_and_opens_no_work_item(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The audit's central defect, closed and asserted on the collection.

    The item was used. The Ferguson standard return requires it not to be, so
    the deterministic evaluator rejects, and the run loop returns before
    `_open_support` is ever reached. A status of `POLICY_REJECTED` on a case that
    had already opened a thread would satisfy a status assertion and fail the
    only one that matters.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "used"]
    facts.append(_fact("used", True))

    outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert harness.support.work_items == {}, "a rejected return opened a Support work item"
    assert outcome.status == ReturnCaseStatus.POLICY_REJECTED.value
    assert outcome.work_item_id is None
    assert outcome.policy_decision == PolicyDecisionName.REJECT.value
    assert "open_support_work_item" not in harness.runtime.calls
    assert "draft_support_request" not in harness.runtime.calls
    # The reason is on the case, not only in the return value.
    assert harness.repository.value_of("policy_decision") == "REJECT"
    assert harness.repository.value_of("policy_reason_codes") == "STANDARD_RETURN_CONDITION_FAILED"


async def test_an_eligible_return_is_approved_and_reaches_support(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The other side of the gate. An approval still has to reach a human."""
    outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    assert harness.repository.value_of("policy_decision") == "APPROVE"
    assert len(harness.support.work_items) == 1
    assert ReturnCaseStatus.POLICY_APPROVED.value in harness.repository.statuses
    # Approval is transient: the case ends up waiting on Support, not on policy.
    assert outcome.status == ReturnCaseStatus.AWAITING_SUPPORT.value
    assert harness.runtime.calls.index("evaluate_case_eligibility") < harness.runtime.calls.index(
        "open_support_work_item"
    )


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


async def test_a_failing_evaluator_holds_the_case_and_never_approves(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """A forced evaluator failure is `REVIEW_REQUIRED`, and opens nothing.

    The facts here are the *approvable* ones, so a gate that fell back to "carry
    on" would approve a return the evaluator never assessed. That is the failure
    mode this asserts against, not merely that an exception was caught.
    """

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the evaluator is broken")

    monkeypatch.setattr(activities_module, "evaluate_return_eligibility", _explode)

    outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    assert outcome.status == ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
    assert outcome.policy_state == PolicyGateState.EVALUATION_FAILED.value
    assert outcome.policy_decision is None
    assert harness.support.work_items == {}, "a failed evaluation opened a work item"
    assert harness.repository.value_of("policy_evaluation_state") == "EVALUATION_FAILED"
    assert str(harness.repository.value_of("policy_evaluation_failure")).startswith(
        "POLICY_EVALUATOR_RAISED"
    )
    # And emphatically not an approval, by any reading.
    assert harness.repository.value_of("policy_decision") is None
    assert ReturnCaseStatus.POLICY_APPROVED.value not in harness.repository.statuses


async def test_an_absent_policy_parks_the_case_as_an_operational_failure(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """Distinguishable from review, which is the whole point of plan sect. 7.2.

    A deployment that published no rule set must not look like an evaluator
    quietly queueing every return for a human. It parks, under a different
    status and a named reason, and it does not wait for a supervisor who has
    nothing to decide.
    """
    without_policy = configuration.model_copy(update={"return_eligibility_policy": None})

    outcome, harness = await _run_case(
        monkeypatch, configuration=without_policy, facts=_approvable_facts()
    )

    assert outcome.status == ReturnCaseStatus.RECOVERY_REQUIRED.value
    assert outcome.status != ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
    assert outcome.parked_reason == "RETURN_ELIGIBILITY_POLICY_NOT_PUBLISHED"
    assert outcome.policy_state == PolicyGateState.POLICY_NOT_CONFIGURED.value
    assert harness.support.work_items == {}
    assert harness.repository.value_of("policy_evaluation_state") == "POLICY_NOT_CONFIGURED"


async def test_an_unconfigured_process_parks_rather_than_evaluating_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker registered with no configuration at all takes the same branch."""
    outcome, harness = await _run_case(monkeypatch, configuration=None, facts=_approvable_facts())

    assert outcome.status == ReturnCaseStatus.RECOVERY_REQUIRED.value
    assert harness.support.work_items == {}


# ---------------------------------------------------------------------------
# Tri-state
# ---------------------------------------------------------------------------


async def test_a_fact_nobody_stated_is_unknown_and_does_not_approve(
    monkeypatch: pytest.MonkeyPatch, reviewing_configuration: ReturnPlatformConfiguration
) -> None:
    """`known false != not mentioned`, at the workflow boundary.

    Every fact but `installed` is stated. A defaulted boolean would read the
    silence as "not installed" and approve; the tri-state reads it as unknown and
    asks a human. The case is otherwise identical to the approving one above,
    which is what makes the comparison worth anything.

    Run against a release that has not narrowed. Under the shipped one the
    silence no longer *objects* -- see the test below -- but it must still never
    be read as evidence, and this is where that stays asserted.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]

    outcome, harness = await _run_case(
        monkeypatch, configuration=reviewing_configuration, facts=facts
    )

    assert outcome.status == ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
    assert outcome.policy_decision == PolicyDecisionName.REVIEW_REQUIRED.value
    assert harness.repository.value_of("policy_reason_codes") == "REQUIRED_FACT_UNKNOWN"
    assert harness.support.work_items == {}


async def test_the_shipped_release_approves_the_same_case_and_records_what_it_skipped(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The operator's narrowing, at the workflow boundary, with its receipt.

    Same case as above. The shipped release sets `NOT_EVALUATED`, so an
    unanswered question stops being an objection and the return window decides
    -- but the approval is not allowed to look like one taken on full evidence.
    `policy_applied_rules` is the fact an audit reads, so the marker has to be
    in it, and `NEW_RESALEABLE_CONDITION` has to be absent from it.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]

    outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert outcome.policy_decision == PolicyDecisionName.APPROVE.value
    applied = str(harness.repository.value_of("policy_applied_rules")).split(",")
    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED.value in applied
    assert PolicyRule.NEW_RESALEABLE_CONDITION.value not in applied
    assert PolicyRule.WITHIN_30_DAYS.value in applied


async def test_a_model_inferred_fact_is_not_evidence(
    monkeypatch: pytest.MonkeyPatch, reviewing_configuration: ReturnPlatformConfiguration
) -> None:
    """An `INFERRED` fact cannot complete an approval.

    The model is advisory (plan sect. 2.2). A case whose only statement about
    installation is a model's guess is a case where nobody has established
    whether it was installed, and it must reach review rather than approval.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]
    facts.append(_fact("installed", False, acquisition="INFERRED"))

    outcome, harness = await _run_case(
        monkeypatch, configuration=reviewing_configuration, facts=facts
    )

    assert outcome.policy_decision == PolicyDecisionName.REVIEW_REQUIRED.value
    assert harness.support.work_items == {}
    excluded = str(harness.repository.value_of("policy_facts_excluded"))
    assert "installed:PENDING_VALIDATION" in excluded


async def test_a_model_inferred_fact_is_still_not_evidence_under_the_narrowing(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The narrowing changed what silence decides. It did not admit the model.

    Under the shipped release this case approves on the window -- and the record
    still says the only statement about installation was a model's guess, that
    the guess was not admitted, and that the check was therefore never applied.
    An approval reached this way must not be readable as one where somebody
    established the item was uninstalled.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]
    facts.append(_fact("installed", False, acquisition="INFERRED"))

    outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert outcome.policy_decision == PolicyDecisionName.APPROVE.value
    excluded = str(harness.repository.value_of("policy_facts_excluded"))
    assert "installed:PENDING_VALIDATION" in excluded
    applied = str(harness.repository.value_of("policy_applied_rules")).split(",")
    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED.value in applied


async def test_a_superseded_fact_is_not_evidence(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """A correction removes the fact it corrects from the evaluation.

    The associate first said the item was unused and then corrected themselves.
    The correction supersedes, the original stops being evidence, and the case
    does not approve on a reading the customer has already withdrawn.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "used"]
    facts.append(_fact("used", False, fact_id="used-first"))
    facts.append(
        _fact(
            "used",
            True,
            fact_id="used-corrected",
            recorded_at=NOW - timedelta(minutes=1),
            supersedes="used-first",
        )
    )

    outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert outcome.policy_decision == PolicyDecisionName.REJECT.value
    assert harness.support.work_items == {}


# ---------------------------------------------------------------------------
# Non-standard routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "route"),
    [
        ("MANUFACTURING_DEFECT", PolicyRouteName.WARRANTY.value),
        ("SHIPPING_DAMAGE", PolicyRouteName.DELIVERY_CLAIM.value),
    ],
)
async def test_a_routed_case_reaches_support_and_is_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
    configuration: ReturnPlatformConfiguration,
    reason: str,
    route: str,
) -> None:
    """Warranty and delivery claims hand to Support. Neither ends the case.

    Both carry no decision -- Support verifies them -- and both open a work item,
    which is precisely the difference from a rejection. A routed *terminal*
    status would end a case halfway through its own happy path, which is what
    the plan's final amendment removed.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "return_reason"]
    facts.append(_fact("return_reason", reason))
    facts.append(_fact("delivery_date", "2026-08-14T09:00:00+00:00"))

    outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert outcome.policy_route == route
    assert outcome.policy_decision is None, "a verification hand-off is not a decision"
    assert len(harness.support.work_items) == 1, "the route never reached Support"
    assert outcome.status == ReturnCaseStatus.AWAITING_SUPPORT.value
    assert outcome.status not in {
        ReturnCaseStatus.POLICY_REJECTED.value,
        ReturnCaseStatus.CLOSED.value,
    }
    assert harness.repository.value_of("policy_route") == route


async def test_a_route_whose_queue_is_undeclared_still_reaches_support(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The release declares no `WARRANTY_SUPPORT` queue yet (3A.6 adds it).

    Until it does, the work item lands on the support service's default queue
    with the route recorded on the case. A queue name the deployment never
    declared would be a work item nobody is looking at, which is worse than one
    on the ordinary queue.
    """
    assert "WARRANTY_SUPPORT" not in configuration.support.queues
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "return_reason"]
    facts.append(_fact("return_reason", "MANUFACTURING_DEFECT"))

    _outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    (work_item,) = harness.support.work_items.values()
    assert work_item["queue"] == "RETURNS_SUPPORT"
    assert harness.repository.value_of("policy_route") == PolicyRouteName.WARRANTY.value


async def test_a_declared_route_queue_is_used(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """With the queue declared, the verification goes to the team that does it."""
    with_queues = configuration.model_copy(
        update={
            "support": configuration.support.model_copy(
                update={"queues": (*configuration.support.queues, "WARRANTY_SUPPORT")}
            )
        }
    )
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "return_reason"]
    facts.append(_fact("return_reason", "MANUFACTURING_DEFECT"))

    _outcome, harness = await _run_case(monkeypatch, configuration=with_queues, facts=facts)

    (work_item,) = harness.support.work_items.values()
    assert work_item["queue"] == "WARRANTY_SUPPORT"


# ---------------------------------------------------------------------------
# D2: the delivery-claim reporting window reaches the work item
#
# `reporting_window: { business_days: 2, basis: DELIVERY_DATE }` was written to
# the case as `policy_delivery_claim_reporting_deadline` and read by nothing, so
# every claim opened on the generic `support_acknowledgement` SLA -- a live run
# recorded a claim opened at 15:59:17 due at 16:04:17, five minutes, where the
# customer's window was two business days.
# ---------------------------------------------------------------------------


def _claim_facts(*, delivery_date: str | None = "2026-08-15T09:00:00+00:00") -> list[dict[str, Any]]:
    """An approvable return re-reasoned as a shipping-damage delivery claim.

    The default delivery date is the morning of `NOW`, so the two-business-day
    window is still open and the claim is `WITHIN` it -- the ordinary shape.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "return_reason"]
    facts.append(_fact("return_reason", "SHIPPING_DAMAGE"))
    if delivery_date is not None:
        facts.append(_fact("delivery_date", delivery_date))
    return facts


def _mon_fri(configuration: ReturnPlatformConfiguration) -> ReturnPlatformConfiguration:
    """The shipped release with the real Mon-Fri desk calendar restored.

    The dev release declares `default` as 24/7 deliberately, which makes
    business days and wall-clock days the same thing and would let a wall-clock
    computation pass for a business-day one. This puts the difference back so
    the test can measure it.
    """
    (declared,) = [
        calendar for calendar in configuration.business_calendars if calendar.calendar_id == "default"
    ]
    weekdays = tuple(
        period.model_copy(update={"weekday": weekday, "start_minute": 540, "end_minute": 1020})
        for weekday in range(5)
        for period in declared.working_periods[:1]
    )
    return configuration.model_copy(
        update={
            "business_calendars": (
                *[
                    calendar
                    for calendar in configuration.business_calendars
                    if calendar.calendar_id != "default"
                ],
                declared.model_copy(update={"working_periods": weekdays}),
            )
        }
    )


async def test_a_delivery_claim_work_item_is_due_on_the_reporting_window(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The configured window reaches `slaDueAt`, and the desk SLA does not.

    Asserted against the deadline the evaluation *recorded*, not against a
    literal: the point of D2 is that one computation reaches the work item, so
    a test naming its own instant would pass even if the two had diverged.
    """
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_claim_facts()
    )

    recorded = harness.repository.value_of("policy_delivery_claim_reporting_deadline")
    assert recorded is not None, "the evaluation computed no deadline to carry"
    (work_item,) = harness.support.work_items.values()
    assert work_item["slaDueAt"] == datetime.fromisoformat(str(recorded))

    # And emphatically not the generic desk SLA, which is what it used to be.
    desk_sla = configuration.workflow.sla_minutes["support_acknowledgement"]
    assert work_item["slaDueAt"] != NOW + timedelta(minutes=desk_sla)
    assert harness.repository.value_of("policy_delivery_claim_window_state") == "WITHIN"
    assert harness.repository.value_of("support_sla_basis") == "DELIVERY_CLAIM_REPORTING_WINDOW"


async def test_a_claim_reported_out_of_window_is_due_when_the_window_closed(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """An elapsed window is still the window, and the work item says so.

    The alternative -- quietly substituting the desk SLA once the deadline is in
    the past -- would put a late claim at the back of a queue sorted on
    `slaDueAt` and lose the only field that says it was late. `ELAPSED` is on
    the case as a reason code; the deadline it elapsed against belongs on the
    work item beside it.
    """
    _outcome, harness = await _run_case(
        monkeypatch,
        configuration=configuration,
        facts=_claim_facts(delivery_date="2026-08-01T09:00:00+00:00"),
    )

    assert harness.repository.value_of("policy_delivery_claim_window_state") == "ELAPSED"
    recorded = harness.repository.value_of("policy_delivery_claim_reporting_deadline")
    (work_item,) = harness.support.work_items.values()
    assert work_item["slaDueAt"] == datetime.fromisoformat(str(recorded))
    assert work_item["slaDueAt"] < NOW
    assert harness.repository.value_of("support_sla_basis") == "DELIVERY_CLAIM_REPORTING_WINDOW"


async def test_the_reporting_window_is_counted_in_business_days(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """Friday delivery, Mon-Fri desk: due at close on Tuesday, not on Sunday.

    The dev release runs a 24/7 calendar on purpose, under which two business
    days and two wall-clock days are the same instant -- so on the shipped
    configuration this deadline is indistinguishable from wall clock and the
    conflation would go unmeasured. Restoring the Mon-Fri pattern is what makes
    the two answers differ, and the assertion is that the work item carries the
    business-day one.
    """
    friday = datetime(2026, 8, 14, 13, 0, tzinfo=UTC)
    _outcome, harness = await _run_case(
        monkeypatch,
        configuration=_mon_fri(configuration),
        facts=_claim_facts(delivery_date=friday.isoformat()),
    )

    (work_item,) = harness.support.work_items.values()
    due = work_item["slaDueAt"]
    assert due is not None
    # 17:00 New York on Tuesday 18 August: Saturday and Sunday are not working
    # days, so day one is Monday and day two is Tuesday.
    assert due == datetime(2026, 8, 18, 21, 0, tzinfo=UTC)
    # Wall clock would have said Sunday the 16th. The gap is the whole point.
    assert due != friday + timedelta(days=2)
    assert harness.repository.value_of("support_sla_basis") == "DELIVERY_CLAIM_REPORTING_WINDOW"


async def test_an_undeterminable_window_keeps_the_desk_sla_and_says_that_it_did(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """No delivery date, no deadline -- and the fallback is legible as one.

    Nothing is invented: the evaluation records `UNDETERMINED`, the activity
    supplies no deadline, and the work item falls back to the desk's own SLA.
    What makes that honest rather than merely silent is `support_sla_basis`,
    which distinguishes this work item from one whose window was computed.
    """
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_claim_facts(delivery_date=None)
    )

    assert harness.repository.value_of("policy_delivery_claim_window_state") == "UNDETERMINED"
    assert harness.repository.value_of("policy_delivery_claim_reporting_deadline") is None
    (work_item,) = harness.support.work_items.values()
    assert work_item["slaDueAt"] is None, "a deadline was invented for a window nobody could compute"
    assert (
        harness.repository.value_of("support_sla_basis")
        == "DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED"
    )


async def test_an_ordinary_return_stays_on_the_desk_sla_and_records_that(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The window is a delivery-claim rule. Nothing else acquires a new deadline."""
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    (work_item,) = harness.support.work_items.values()
    assert work_item["slaDueAt"] is None
    assert harness.repository.value_of("support_sla_basis") == "SUPPORT_ACKNOWLEDGEMENT"


async def test_a_support_service_that_cannot_take_the_deadline_is_not_recorded_as_computed(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """A dropped deadline must not leave a case claiming it has one.

    The wiring fault is loud in the log and honest on the case: the work item
    got the desk SLA, so the basis says the desk SLA -- beside
    `policy_delivery_claim_window_state: WITHIN`, which is what makes the pair
    readable as the anomaly it is rather than as an undetermined window.
    """
    _outcome, harness = await _run_case(
        monkeypatch,
        configuration=configuration,
        facts=_claim_facts(),
        support=_SupportServiceWithoutSla(),
    )

    assert harness.repository.value_of("policy_delivery_claim_window_state") == "WITHIN"
    assert harness.repository.value_of("support_sla_basis") == "SUPPORT_ACKNOWLEDGEMENT"


# ---------------------------------------------------------------------------
# The supervisor override
# ---------------------------------------------------------------------------


async def test_an_unanswered_review_parks_without_opening_a_work_item(
    monkeypatch: pytest.MonkeyPatch, reviewing_configuration: ReturnPlatformConfiguration
) -> None:
    """Nobody answered. The case is parked, and Support was still never asked.

    A reviewing release, because this is a test about what happens to a case
    already in review rather than about how it got there.
    """
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]

    outcome, harness = await _run_case(
        monkeypatch, configuration=reviewing_configuration, facts=facts
    )

    assert outcome.status == ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
    assert outcome.parked_reason == "POLICY_REVIEW_UNANSWERED"
    assert harness.support.work_items == {}


async def test_an_approving_override_lets_the_case_through(
    monkeypatch: pytest.MonkeyPatch, reviewing_configuration: ReturnPlatformConfiguration
) -> None:
    """A supervisor approves what the evaluator would not, and the case moves.

    The override is what the endpoint signals. Its `actor` and timestamp are
    already server-derived by the time they reach here -- the workflow records
    the notice and never invents attribution of its own.

    A reviewing release, so that there is something to override. Under the
    shipped one this case approves on its own, which is the operator's point and
    would leave this test asserting nothing.
    """
    configuration = reviewing_configuration
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]
    holder: list[ReturnCaseWorkflow] = []
    notice = PolicyOverrideNotice(
        override_decision=PolicyDecisionName.APPROVE.value,
        reason_code="SUPERVISOR_JUDGEMENT",
        actor="supervisor-1",
        overridden_at_iso=NOW.isoformat(),
        reason="Customer produced the packing slip.",
        idempotency_key="override-key-1",
    )

    repository = _Repository(facts)
    support = _SupportService()
    activities = ReturnCaseActivities(
        repository=repository,  # type: ignore[arg-type]
        support_service=support,
        configuration=lambda: configuration,
    )
    runtime = _Runtime(
        {
            "record_case_status": activities.record_case_status,
            "resolve_business_deadline": activities.resolve_business_deadline,
            "request_bay_assignment": activities.request_bay_assignment,
            "evaluate_case_eligibility": activities.evaluate_case_eligibility,
            "draft_support_request": activities.draft_support_request,
            "open_support_work_item": activities.open_support_work_item,
            "send_support_reminder": activities.send_support_reminder,
        },
        arrivals=[lambda: holder[0].policy_override(notice)],
    )
    monkeypatch.setattr(workflow_module, "workflow", runtime)
    instance = ReturnCaseWorkflow()
    holder.append(instance)

    outcome = await instance.run(
        ReturnCaseWorkflowInput(
            case_id=CASE_ID,
            tenant_id="tenant-a",
            principal_id="associate-1",
            conversation_id="conv-1",
            configuration_release_id="release-1",
            timings=_timings(),
        )
    )

    assert outcome.policy_overridden is True
    assert len(support.work_items) == 1, "an approved override did not reach Support"
    assert ReturnCaseStatus.POLICY_APPROVED.value in repository.statuses
    # The evaluator's own answer survives the override.
    assert repository.value_of("policy_decision") == "REVIEW_REQUIRED"
    assert outcome.policy_decision == PolicyDecisionName.REVIEW_REQUIRED.value


async def test_a_rejecting_override_is_terminal_and_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, reviewing_configuration: ReturnPlatformConfiguration
) -> None:
    configuration = reviewing_configuration
    facts = [fact for fact in _approvable_facts() if fact["factName"] != "installed"]
    holder: list[ReturnCaseWorkflow] = []
    notice = PolicyOverrideNotice(
        override_decision=PolicyDecisionName.REJECT.value,
        reason_code="SUPERVISOR_JUDGEMENT",
        actor="supervisor-1",
        overridden_at_iso=NOW.isoformat(),
    )

    repository = _Repository(facts)
    support = _SupportService()
    activities = ReturnCaseActivities(
        repository=repository,  # type: ignore[arg-type]
        support_service=support,
        configuration=lambda: configuration,
    )
    runtime = _Runtime(
        {
            "record_case_status": activities.record_case_status,
            "resolve_business_deadline": activities.resolve_business_deadline,
            "request_bay_assignment": activities.request_bay_assignment,
            "evaluate_case_eligibility": activities.evaluate_case_eligibility,
            "draft_support_request": activities.draft_support_request,
            "open_support_work_item": activities.open_support_work_item,
            "send_support_reminder": activities.send_support_reminder,
        },
        arrivals=[lambda: holder[0].policy_override(notice)],
    )
    monkeypatch.setattr(workflow_module, "workflow", runtime)
    instance = ReturnCaseWorkflow()
    holder.append(instance)

    outcome = await instance.run(
        ReturnCaseWorkflowInput(
            case_id=CASE_ID,
            tenant_id="tenant-a",
            principal_id="associate-1",
            conversation_id="conv-1",
            configuration_release_id="release-1",
            timings=_timings(),
        )
    )

    assert outcome.status == ReturnCaseStatus.POLICY_REJECTED.value
    assert support.work_items == {}


async def test_an_override_that_is_not_a_decision_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`REVIEW_REQUIRED` is the state, not an override of it.

    Refused in the signal handler as well as at the endpoint, because a signal
    can be sent by anything that holds the workflow id.
    """
    monkeypatch.setattr(workflow_module, "workflow", _Runtime({}))
    instance = ReturnCaseWorkflow()
    instance.policy_override(
        PolicyOverrideNotice(
            override_decision="REVIEW_REQUIRED",
            reason_code="NOISE",
            actor="supervisor-1",
            overridden_at_iso=NOW.isoformat(),
        )
    )

    assert instance._state.policy_override is None


async def test_the_first_override_wins() -> None:
    """A supervisor clicking twice must not decide the case twice."""
    instance = ReturnCaseWorkflow()
    first = PolicyOverrideNotice(
        override_decision="APPROVE",
        reason_code="FIRST",
        actor="supervisor-1",
        overridden_at_iso=NOW.isoformat(),
    )
    instance.policy_override(first)
    instance.policy_override(
        PolicyOverrideNotice(
            override_decision="REJECT",
            reason_code="SECOND",
            actor="supervisor-2",
            overridden_at_iso=NOW.isoformat(),
        )
    )

    assert instance._state.policy_override is first


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


async def test_the_decision_is_persisted_with_its_whole_provenance(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """Which release decided, from which document, under which rules (3A.8).

    A decision nobody can trace to a published rule set is a decision nobody can
    defend, which is the state the audit found the platform in -- a policy code
    typed into a React component.
    """
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    assert harness.repository.value_of("policy_id") == "ferguson-standard-return-policy"
    assert harness.repository.value_of("policy_version") == "2026-08-15"
    assert harness.repository.value_of("policy_authority") == "FERGUSON_PUBLIC_TERMS"
    assert (
        harness.repository.value_of("policy_source_document")
        == "Ferguson Terms and Conditions of Sale"
    )
    assert "Rev. May 2025" in str(harness.repository.value_of("policy_source_revision"))
    assert harness.repository.value_of("policy_configuration_release_id") == "release-1"
    assert harness.repository.value_of("policy_evaluated_at") == NOW.isoformat()
    applied = str(harness.repository.value_of("policy_applied_rules")).split(",")
    assert "POLICY_RELEASE_VALIDATED" in applied
    assert "WITHIN_30_DAYS" in applied
    # Every persisted value is a scalar: `CaseFactProjection.value` is typed
    # `str | int | float | bool | None`, so a list would parse here and fail the
    # projection the console reads.
    for document in harness.repository.facts:
        if str(document["factName"]).startswith("policy_"):
            assert isinstance(document["value"], (str, int, float, bool))


async def test_a_replayed_evaluation_writes_one_set_of_facts(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The activity is retried with identical input, including its fact seed.

    The log is insert-only against a unique `factId`, so a retry that minted new
    ids would leave two opinions about one evaluation and `latest_case_facts`
    would resolve them by whichever landed last.
    """
    repository = _Repository(_approvable_facts())
    activities = ReturnCaseActivities(
        repository=repository,  # type: ignore[arg-type]
        support_service=_SupportService(),
        configuration=lambda: configuration,
    )
    request = workflow_module.EvaluateCaseEligibilityInput(
        case_id=CASE_ID,
        tenant_id="tenant-a",
        configuration_release_id="release-1",
        evaluated_at_iso=NOW.isoformat(),
        business_calendar_id="default",
        timezone="UTC",
        fact_id_seed="seed-1",
    )

    first = await activities.evaluate_case_eligibility(request)
    written = len(repository.facts)
    second = await activities.evaluate_case_eligibility(request)

    assert first == second
    assert len(repository.facts) == written, "a retry wrote a second set of policy facts"
