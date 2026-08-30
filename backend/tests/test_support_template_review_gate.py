"""V1: the review gate inside `_open_support` (contracts.md sect. 6).

Driven through the **shipped run loop**. `ReturnCaseWorkflow` is an ordinary
class that reaches the outside world through a handful of `temporalio.workflow`
functions, so replacing those drives the real `_open_support`, the real wait
loop and the real deadline branch -- the same substitution
`tests/policy/test_case_policy_gate.py` documents and for the same reason.

Behind the activity names sits the **real** `SupportTemplateGateService` over
the real `ReviewAggregateStore` and the Mongo double, so "the reviewer approved
and the message went out" is a fact about the aggregate rather than about a
mock's call list. What is doubled is one layer: the activity methods'
*assembly* of the render input from the case projection, which needs a whole
operational repository and is exercised by the draft tests instead.

The four claims this file exists to make, each with the fault injected:

1. **Un-patched histories are untouched.** Covered next door in
   `test_return_case_workflow_replay_compatibility.py`, which drives the same
   method with `patched -> False` and asserts the activity sequence is
   byte-identically the pre-gate one.
2. **A held record does not block an approved one.** One map, one wait.
3. **A gap forces the hold even under `on_timeout: auto_send`.**
4. **Nothing is sent without an approval**, and a `continue_as_new` in the
   middle of the wait does not restart the reviewer's clock or re-open the
   review they are reading.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.support_gate_configuration import (
    SupportGateConfiguration,
    TemplateReviewConfiguration,
    TemplateReviewTimeoutPolicy,
)
from return_platform.operations import fact_names
from return_platform.operations.case_commands import (
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.models import FactAcquisition
from return_platform.operations.review_aggregate import (
    ReviewAggregateStore,
    ReviewState,
    TemplateReviewParkReason,
    canonical_review_payload,
    ensure_review_indexes,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.operations.support_template_draft import SAMPLE_CASE, draft_facts
from return_platform.operations.support_template_gate import (
    MongoDraftEditRows,
    SupportTemplateGateService,
)
from return_platform.workflows import return_case_workflow as workflow_module
from return_platform.workflows.return_case_launcher import case_timings_from_configuration
from return_platform.workflows.return_case_workflow import (
    ReturnCaseStatus,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SnapshotSentTemplateInput,
    TemplateDeliveryResult,
    TemplateReviewDraftInput,
    TemplateReviewDraftResult,
    TemplateReviewDraftSet,
    TemplateReviewNotice,
    TemplateReviewRevisionInput,
)
from tests.operations.scoped_fact_double import ScopedFactDouble

_async = pytest.mark.asyncio

PRODUCTION_YAML = Path(__file__).resolve().parents[1] / "config" / "returns" / "production.yaml"
CASE_ID = "case-gate-wf"
REQUEST_ID = f"support:{CASE_ID}"
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# The runtime substitution
# --------------------------------------------------------------------------- #


class _Info:
    @staticmethod
    def is_continue_as_new_suggested() -> bool:
        return False


class _SuggestingInfo:
    @staticmethod
    def is_continue_as_new_suggested() -> bool:
        return True


class _ContinueAsNew(Exception):
    def __init__(self, workflow_input: ReturnCaseWorkflowInput) -> None:
        super().__init__("continue_as_new")
        self.workflow_input = workflow_input


class _Runtime:
    """The `temporalio.workflow` functions `_open_support` and the gate call.

    `wait_condition` is where the shape of the test lives: each call first runs
    the next scheduled arrival -- a signal landing mid-wait -- then answers the
    predicate, and otherwise advances the clock by the timeout and raises
    `TimeoutError`, which is exactly what the gate's `except TimeoutError`
    expects. Advancing the clock is what makes a bounded wait terminate rather
    than spin.
    """

    def __init__(
        self,
        activities: dict[str, Callable[[Any], Awaitable[Any]]],
        *,
        arrivals: list[Callable[[], Any]] | None = None,
        patches: bool = True,
        suggest_continue_as_new: bool = False,
    ) -> None:
        self._activities = activities
        self._arrivals = list(arrivals or [])
        self._uuid = 0
        self._patches = patches
        self._suggest = suggest_continue_as_new
        self.calls: list[str] = []
        self.patch_ids: list[str] = []
        self.instant = NOW
        self.logger = logging.getLogger("tests.gate")

    async def execute_activity(self, name: str, argument: Any, **_options: Any) -> Any:
        self.calls.append(name)
        return await self._activities[name](argument)

    def patched(self, patch_id: str) -> bool:
        self.patch_ids.append(patch_id)
        return self._patches

    def now(self) -> datetime:
        return self.instant

    def uuid4(self) -> uuid.UUID:
        self._uuid += 1
        return uuid.UUID(int=self._uuid)

    async def wait_condition(
        self,
        predicate: Callable[[], bool],
        *,
        timeout: timedelta | None = None,  # noqa: ASYNC109 - mirrors the real signature
        timeout_summary: str | None = None,
    ) -> None:
        del timeout_summary
        if self._arrivals:
            # Awaited when it is a coroutine function. A reviewer approving is
            # a write to the aggregate followed by a signal, and scheduling the
            # write for *after* the wait would make every "and then it was
            # sent" assertion below true of a send that never raced anything.
            arriving = self._arrivals.pop(0)()
            if inspect.isawaitable(arriving):
                await arriving
        if predicate():
            return
        self.instant += timeout if timeout is not None else timedelta(seconds=1)
        raise TimeoutError

    def info(self) -> Any:
        return _SuggestingInfo() if self._suggest else _Info()

    @staticmethod
    def all_handlers_finished() -> bool:
        return True

    @staticmethod
    def continue_as_new(workflow_input: ReturnCaseWorkflowInput) -> None:
        raise _ContinueAsNew(workflow_input)


# --------------------------------------------------------------------------- #
# The real gate, behind doubled activity names
# --------------------------------------------------------------------------- #


class _Thread:
    def __init__(self, created: bool) -> None:
        self.workItemId = "wi-gate-1"  # noqa: N815 - the wire name
        self.threadId = "th-gate-1"  # noqa: N815
        self.created = created


class _Post:
    absorbed = False


class _Support:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.opened = 0

    async def ensure_case_support_thread(self, **kwargs: Any) -> _Thread:
        del kwargs
        self.opened += 1
        return _Thread(created=False)

    async def post_support_message(self, **kwargs: Any) -> _Post:
        self.posted.append(dict(kwargs))
        return _Post()


class _GateActivities:
    """The four activity names, over the real service.

    The one thing doubled here is `_render_inputs` -- turning a case into the
    fact map a render reads -- because that needs an operational repository
    this file has no reason to build. Everything downstream of it is production
    code: the renderer, the payload, the review aggregate, the approval
    transition, the delivery identity.
    """

    def __init__(
        self,
        gate: SupportTemplateGateService,
        *,
        facts: dict[tuple[str | None, str], dict[str, Any]] | None = None,
        request_ids: tuple[str, ...] = (REQUEST_ID,),
    ) -> None:
        self._gate = gate
        self._facts = facts if facts is not None else draft_facts(**SAMPLE_CASE)
        #: What the grouping resolved to. Parameterised so the map-based wait
        #: can be driven with two requests, which is the only way "a held
        #: record does not block an approved one" is a falsifiable claim.
        self._request_ids = request_ids

    def table(self) -> dict[str, Callable[[Any], Awaitable[Any]]]:
        return {
            "record_case_status": self.record_case_status,
            "resolve_business_deadline": self.resolve_business_deadline,
            "draft_support_request": self.draft_support_request,
            "open_support_work_item": self.open_support_work_item,
            "record_template_draft": self.record_template_draft,
            "rerender_template_draft": self.rerender_template_draft,
            "record_template_revision": self.record_template_revision,
            "snapshot_sent_template": self.snapshot_sent_template,
            # AMENDMENT-5 rule 2. Over the **real** gate service, like the four
            # above: a double that answered "held nothing" would make every
            # assertion below true of a gate that parks nothing at all.
            "hold_unsettled_reviews": self.hold_unsettled_reviews,
        }

    async def hold_unsettled_reviews(self, request: Any) -> Any:
        from return_platform.workflows.return_case_workflow import HoldUnsettledReviewsResult

        return HoldUnsettledReviewsResult(
            held_review_ids=await self._gate.hold_unsettled(case_id=request.case_id)
        )

    async def record_case_status(self, request: Any) -> None:
        del request

    async def resolve_business_deadline(self, request: Any) -> Any:
        from return_platform.workflows.return_case_workflow import ResolvedBusinessDeadline

        start = datetime.fromisoformat(request.from_iso)
        return ResolvedBusinessDeadline(
            instant_iso=(start + timedelta(seconds=request.working_seconds)).isoformat(),
            calendar_applied=True,
        )

    async def draft_support_request(self, request: Any) -> Any:
        from return_platform.workflows.return_case_workflow import SupportRequestDraft

        del request
        return SupportRequestDraft(text="composed", payload={}, subject="composed")

    async def open_support_work_item(self, request: Any) -> str:
        del request
        return "wi-straight-through"

    async def record_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftSet:
        drafts: list[TemplateReviewDraftResult] = []
        for index, request_id in enumerate(self._request_ids):
            draft = await self._gate.record_draft(
                case_id=request.case_id,
                request_id=request_id,
                review_id=f"{request.fact_id_seed}:{index}",
                fact_id_seed=f"{request.fact_id_seed}:{index}",
                facts=self._facts,
            )
            if not draft.template_available:
                return TemplateReviewDraftSet(template_available=False)
            drafts.append(
                TemplateReviewDraftResult(
                    request_id=request_id,
                    review_id=draft.review_id or "",
                    state=draft.state,
                    draft_version=draft.draft_version,
                    canonical_edit_version=draft.canonical_edit_version,
                    gap_field_ids=draft.gap_field_ids,
                )
            )
        return TemplateReviewDraftSet(drafts=tuple(drafts))

    async def rerender_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftResult:
        draft = await self._gate.rerender_draft(
            case_id=request.case_id,
            request_id=request.request_id,
            review_id=request.review_id,
            fact_id_seed=request.fact_id_seed,
            facts=self._facts,
        )
        return TemplateReviewDraftResult(
            request_id=request.request_id,
            review_id=draft.review_id or request.review_id,
            state=draft.state,
            draft_version=draft.draft_version,
            canonical_edit_version=draft.canonical_edit_version,
            gap_field_ids=draft.gap_field_ids,
        )

    async def record_template_revision(self, request: TemplateReviewRevisionInput) -> None:
        await self._gate.record_revision(
            case_id=request.case_id,
            review_id=request.review_id,
            actor_id=request.actor_id,
            note=request.note,
            fact_id_seed=request.fact_id_seed,
        )

    async def snapshot_sent_template(
        self, request: SnapshotSentTemplateInput
    ) -> TemplateDeliveryResult:
        # The real activity's body, minus the `self._gate()` lookup. Kept in
        # step with it by `test_the_activity_and_this_double_take_one_path`.
        from return_platform.workflows.return_case_activities import ReturnCaseActivities

        activities = ReturnCaseActivities(
            repository=cast(Any, None),
            support_service=cast(Any, None),
            template_gate=self._gate,
        )
        return await activities.snapshot_sent_template(request)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(PRODUCTION_YAML).configuration


@pytest.fixture
def mongo() -> Any:
    from tests.operations.mongo_double import FakeClient

    return FakeClient()


@pytest_asyncio.fixture
async def store(mongo: Any, test_settings: Settings) -> ReviewAggregateStore:
    database = mongo[test_settings.mongo_database]
    await ensure_review_indexes(database)
    await ensure_case_command_indexes(database)
    return ReviewAggregateStore(
        mongo, test_settings, command_store=DurableCaseCommandStore(mongo, test_settings)
    )


def _gate_service(
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
    support: _Support,
    facts: ScopedFactDouble | None = None,
) -> SupportTemplateGateService:
    return SupportTemplateGateService(
        reviews=store,
        edit_rows=MongoDraftEditRows(mongo[test_settings.mongo_database]),
        support_service=support,
        configuration=lambda: configuration,
        append_fact=facts or ScopedFactDouble(),
    )


def _timings(configuration: ReturnPlatformConfiguration, **gate: Any) -> ReturnCaseTimings:
    """Timings built by the **shipped** builder, not by hand.

    Constructing `ReturnCaseTimings(...)` directly here would let the gate
    fields drift out of the release without any test noticing -- which is the
    hole condition 3 was, one layer up.
    """
    block = SupportGateConfiguration(template_review=TemplateReviewConfiguration(**gate))
    return case_timings_from_configuration(configuration.return_case, block)


def _input(timings: ReturnCaseTimings, **overrides: Any) -> ReturnCaseWorkflowInput:
    base: dict[str, Any] = {
        "case_id": CASE_ID,
        "tenant_id": "tenant-a",
        "principal_id": "dev-operator",
        "conversation_id": "conv-1",
        "configuration_release_id": "release-1",
        "timings": timings,
    }
    base.update(overrides)
    return ReturnCaseWorkflowInput(**base)


async def _run_gate(
    monkeypatch: pytest.MonkeyPatch,
    activities: _GateActivities,
    timings: ReturnCaseTimings,
    *,
    arrivals: list[Callable[[], Any]] | None = None,
    holder: list[ReturnCaseWorkflow] | None = None,
    patches: bool = True,
    suggest_continue_as_new: bool = False,
    workflow_input: ReturnCaseWorkflowInput | None = None,
) -> tuple[ReturnCaseWorkflow, _Runtime]:
    runtime = _Runtime(
        activities.table(),
        arrivals=arrivals,
        patches=patches,
        suggest_continue_as_new=suggest_continue_as_new,
    )
    monkeypatch.setattr(workflow_module, "workflow", runtime)
    instance = ReturnCaseWorkflow()
    if holder is not None:
        holder.append(instance)
    instance._input = workflow_input or _input(timings)  # noqa: SLF001 - the run loop's own field
    resumed = instance._input.resumed_template_reviews  # noqa: SLF001
    instance._state.template_reviews = dict(resumed)  # noqa: SLF001
    await instance._open_support(timings)  # noqa: SLF001
    return instance, runtime


def _notice(review_id: str, **overrides: Any) -> TemplateReviewNotice:
    base: dict[str, Any] = {
        "review_id": review_id,
        "request_id": REQUEST_ID,
        "actor": "associate-a",
        "signal_id": f"sig-{review_id}",
        "draft_version": 1,
        "canonical_edit_version": 0,
    }
    base.update(overrides)
    return TemplateReviewNotice(**base)


async def _approve(
    store: ReviewAggregateStore, gate: SupportTemplateGateService, review_id: str
) -> None:
    """What the endpoint does: the atomic `OPEN -> APPROVING` with its CAS."""
    review = await store.get_review(case_id=CASE_ID, review_id=review_id)
    await gate.approve(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-a",
        expected_draft_version=int(review["draftVersion"]),
        expected_canonical_edit_version=int(review["canonicalEditVersion"]),
        canonical_approved_payload_hash=canonical_payload_digest(canonical_review_payload(review)),
        workflow_id=f"return-case-{CASE_ID}",
        signal_id=f"sig-{review_id}",
    )


def states_of_sent(reviews: list[dict[str, Any]]) -> set[str]:
    """The ids of every review that reached `SENT`."""
    return {
        str(review["_id"])
        for review in reviews
        if ReviewState(str(review["state"])) is ReviewState.SENT
    }


# --------------------------------------------------------------------------- #
# The gate runs, and the straight-through path does not
# --------------------------------------------------------------------------- #


@_async
async def test_an_approved_review_is_sent_and_no_work_item_is_opened_first(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The whole point of the gate: nothing reaches Support before a person says so.

    `open_support_work_item` must not appear in the call list at all. It is the
    straight-through send, and a gate that ran *and* then also sent would have
    put the same request in front of Support twice.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    activities = _GateActivities(gate)
    holder: list[ReturnCaseWorkflow] = []
    timings = _timings(configuration)

    async def approve_when_open() -> None:
        reviews = await store.list_reviews(CASE_ID)
        await _approve(store, gate, str(reviews[0]["_id"]))
        holder[0].template_approved(_notice(str(reviews[0]["_id"])))

    _instance, runtime = await _run_gate(
        monkeypatch, activities, timings, holder=holder, arrivals=[approve_when_open]
    )

    assert "record_template_draft" in runtime.calls
    assert "snapshot_sent_template" in runtime.calls
    assert "open_support_work_item" not in runtime.calls
    assert len(support.posted) == 1, "the approved draft actually left the platform"
    reviews = await store.list_reviews(CASE_ID)
    assert ReviewState(str(reviews[0]["state"])) is ReviewState.SENT


@_async
async def test_the_gate_is_skipped_when_the_release_turns_it_off(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """`enabled: false` is the pre-gate behaviour exactly -- and it must be, or
    turning the gate off would be a different feature rather than none."""
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    _instance, runtime = await _run_gate(
        monkeypatch, _GateActivities(gate), _timings(configuration, enabled=False)
    )

    assert runtime.calls == [
        "draft_support_request",
        "open_support_work_item",
        "record_case_status",
    ]
    assert await store.list_reviews(CASE_ID) == []


@_async
async def test_a_release_with_no_template_falls_back_to_the_composed_path(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
) -> None:
    """The gate asks, is told there is nothing to review, and hands the case back.

    Not a park. A deployment that has published no template is one the platform
    behaved correctly for before the gate existed, and it must keep behaving
    that way.
    """
    support = _Support()
    gate = SupportTemplateGateService(
        reviews=store,
        edit_rows=MongoDraftEditRows(mongo[test_settings.mongo_database]),
        support_service=support,
        configuration=lambda: None,
        append_fact=ScopedFactDouble(),
    )
    timings = ReturnCaseTimings(
        bay_wait_seconds=0,
        support_response_wait_seconds=300,
        reminder_interval_seconds=60,
        max_reminders=1,
        on_reminders_exhausted="PARK_FOR_OPERATIONS",
        business_calendar_id="default",
        timezone="UTC",
    )
    _instance, runtime = await _run_gate(monkeypatch, _GateActivities(gate), timings)

    assert "record_template_draft" in runtime.calls
    assert "open_support_work_item" in runtime.calls
    assert await store.list_reviews(CASE_ID) == []


# --------------------------------------------------------------------------- #
# The deadline
# --------------------------------------------------------------------------- #


@_async
async def test_nobody_answering_parks_the_case_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """`on_timeout: hold`. The review stays `OPEN` -- a deadline passing does
    not make a draft un-reviewable, and a late reviewer can still answer."""
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
    )

    assert instance._state.parked_reason == "TEMPLATE_REVIEW_UNANSWERED"  # noqa: SLF001
    assert support.posted == []
    reviews = await store.list_reviews(CASE_ID)
    # **AMENDMENT-5 changed this assertion, and the old one was right until it
    # did.** Before the amendment the deadline left every review `OPEN`, on the
    # reasoning that a late reviewer can still answer. That is true and it built
    # a trap: with the gate closed, approving an `OPEN` review CASes it to
    # `APPROVING`, the workflow discards the notice, and `APPROVING`'s three
    # exits are all workflow-driven. The review would be stuck for good.
    #
    # So the gate parks what it was holding, and the late reviewer's path is now
    # the reopen from `HELD_FOR_OPERATIONS` rather than an approve into nothing.
    assert ReviewState(str(reviews[0]["state"])) is ReviewState.HELD_FOR_OPERATIONS


@_async
async def test_escalate_parks_with_the_guard_blocked_reason(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(
            configuration,
            review_wait_seconds=120,
            reminder_interval_seconds=60,
            on_timeout=TemplateReviewTimeoutPolicy.ESCALATE,
        ),
    )

    assert instance._state.parked_reason == "TEMPLATE_REVIEW_GUARD_BLOCKED"  # noqa: SLF001

    assert support.posted == []


@_async
async def test_auto_send_sends_a_clean_draft_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The other half of the gap rule below: without a gap, `auto_send` sends.

    Without this the gap test would pass against a gate that never auto-sent
    anything, which is a different and much less interesting guarantee.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(
            configuration,
            review_wait_seconds=120,
            reminder_interval_seconds=60,
            on_timeout=TemplateReviewTimeoutPolicy.AUTO_SEND,
        ),
    )

    assert len(support.posted) == 1
    reviews = await store.list_reviews(CASE_ID)
    assert ReviewState(str(reviews[0]["state"])) is ReviewState.SENT
    assert str(reviews[0]["approvedBy"]) == "SYSTEM"
    assert instance._state.parked_reason is None  # noqa: SLF001


@_async
async def test_a_gap_forces_the_hold_even_under_auto_send(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Contracts.md sect. 6, the rule with teeth.

    *An unresolved required gap forces hold/escalate regardless of `on_timeout:
    auto_send`.* A gap is the case saying it does not know something the message
    asserts, and sending it anyway is the platform stating a fact nobody
    observed -- to a human who will then act on it.

    The gap is real, not stubbed: the fact set is emptied, so the shipped
    template's required fields genuinely cannot resolve.
    """
    support = _Support()
    facts = ScopedFactDouble()
    gate = _gate_service(store, mongo, test_settings, configuration, support, facts=facts)
    activities = _GateActivities(gate, facts={})
    instance, _runtime = await _run_gate(
        monkeypatch,
        activities,
        _timings(
            configuration,
            review_wait_seconds=120,
            reminder_interval_seconds=60,
            on_timeout=TemplateReviewTimeoutPolicy.AUTO_SEND,
        ),
    )

    reviews = await store.list_reviews(CASE_ID)
    assert reviews[0]["draftPayload"]["gaps"], "the fixture must actually gap"
    assert support.posted == [], "a gapped draft must not reach Support"
    # Parked on close, per AMENDMENT-5 rule 2 -- see
    # `test_nobody_answering_parks_the_case_and_sends_nothing` for why this is
    # no longer `OPEN`. The gap rule itself is unchanged: nothing was sent.
    assert ReviewState(str(reviews[0]["state"])) is ReviewState.HELD_FOR_OPERATIONS
    assert instance._state.parked_reason == "TEMPLATE_REVIEW_GUARD_BLOCKED"  # noqa: SLF001

    # **The gap fact itself** (RV V1p2-1 F1). This is the only test in either
    # suite that reaches `_record_draft_facts`' gap loop, so until now that
    # write's call shape was pinned by nothing -- RV dropped its required
    # `agent_id` and all 51 tests stayed green. Two things are asserted, and
    # the first is what makes the second mean anything: the write *happened*,
    # and it happened with the fields an operator reading the log needs.
    #
    # The call shape is enforced by `ScopedFactDouble`, which binds every write
    # against the repository's real signature -- so a missing `agent_id` now
    # fails here rather than in a worker, on the branch that fires exactly when
    # a human most needs the record of why.
    gap_facts = facts.named(fact_names.SUPPORT_TEMPLATE_GAP)
    assert gap_facts, "a review-blocking gap must leave a record of why"
    assert len(gap_facts) == len(reviews[0]["draftPayload"]["gaps"]), (
        "one fact per gap -- a single fact for several would lose which field"
    )
    for gap_fact in gap_facts:
        assert gap_fact["record_scope"] == str(reviews[0]["_id"]), (
            "scoped to the attempt, so a redraft's gaps are not the first one's"
        )
        assert gap_fact["value"]["field_id"], "a gap that does not name a field is not actionable"
        assert gap_fact["value"]["reason"]
        assert gap_fact["agent_id"] == "support-template-gate"
        assert gap_fact["acquisition_method"] is FactAcquisition.DERIVED


# --------------------------------------------------------------------------- #
# The map
# --------------------------------------------------------------------------- #


@_async
async def test_a_cancelled_review_ends_the_wait_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []

    def cancel() -> None:
        review_id = next(iter(holder[0]._state.template_reviews.values()))  # noqa: SLF001
        holder[0].template_cancelled(_notice(review_id))

    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration),
        holder=holder,
        arrivals=[cancel],
    )

    assert instance._state.parked_reason == "TEMPLATE_REVIEW_CANCELLED"  # noqa: SLF001
    assert support.posted == []


@_async
async def test_a_redraft_repoints_the_wait_and_the_new_attempt_can_be_sent(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The redraft path, end to end -- and it was completely broken.

    `redraft` cancels one attempt and mints another with a **new** `review_id`.
    The wait map still held the old one, so every later decision about the
    request was discarded as "not this case's open attempt" and the case sat
    unanswerable behind a review that had already been cancelled. The producer's
    half -- that the endpoint emits a revision notice carrying `supersedes` --
    is asserted in `tests/api/test_case_panel_and_reviews.py`; this is the
    consumer's.

    Both halves of the outcome are asserted, because either alone is weak: the
    map has to be re-pointed at the new attempt (otherwise the approval below is
    the *old* review's, which is cancelled and would refuse), and the message
    has to actually reach Support.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []
    redrafted: list[str] = []

    async def redraft_then_approve() -> None:
        instance = holder[0]
        original = next(iter(instance._state.template_reviews.values()))  # noqa: SLF001
        current = await store.get_review(case_id=CASE_ID, review_id=original)
        fresh = await store.redraft(
            case_id=CASE_ID,
            review_id=original,
            actor_id="associate-a",
            draft_payload=dict(current["draftPayload"]),
        )
        new_id = str(fresh["_id"])
        assert new_id != original
        redrafted.append(new_id)
        instance.template_revised(_notice(new_id, signal_id="sig-redraft", supersedes=original))

    async def approve_the_new_one() -> None:
        instance = holder[0]
        # Read from the map, so this asserts the re-pointing rather than
        # assuming it: approving `redrafted[0]` directly would pass even if the
        # workflow were still holding the cancelled attempt.
        held = next(iter(instance._state.template_reviews.values()))  # noqa: SLF001
        assert held == redrafted[0], "the wait must be re-pointed at the new attempt"
        await _approve(store, gate, held)
        instance.template_approved(_notice(held, signal_id="sig-approve-new"))

    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=600, reminder_interval_seconds=300),
        holder=holder,
        arrivals=[redraft_then_approve, approve_the_new_one],
    )

    assert len(support.posted) == 1, "the redrafted attempt reached Support"
    assert instance._state.parked_reason is None  # noqa: SLF001
    sent = await store.get_review(case_id=CASE_ID, review_id=redrafted[0])
    assert ReviewState(str(sent["state"])) is ReviewState.SENT


@_async
async def test_a_revision_naming_an_unheld_review_is_still_ignored_without_supersedes(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Re-pointing is a privilege of a notice that names what we are holding.

    Without this, `supersedes` would have widened the router into "any revision
    may claim any request", which is the multi-RMA failure the original guard
    existed to prevent. The notice below is a revision for a review this case
    never held and it names no predecessor, so it is discarded exactly as an
    approval would be -- and the wait runs to its deadline.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []

    def stray() -> None:
        holder[0].template_revised(_notice("review-from-another-case"))

    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
        holder=holder,
        arrivals=[stray],
    )

    assert support.posted == []
    assert instance._state.parked_reason == "TEMPLATE_REVIEW_UNANSWERED"  # noqa: SLF001


@_async
async def test_a_supersedes_naming_a_review_this_case_never_held_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The sharper half: a notice that *does* carry `supersedes`, pointing at
    something this workflow is not holding, must not re-point anything."""
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []

    def stray() -> None:
        holder[0].template_revised(_notice("some-other-attempt", supersedes="never-held-here"))

    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
        holder=holder,
        arrivals=[stray],
    )

    assert support.posted == []
    assert "some-other-attempt" not in instance._state.template_reviews.values()  # noqa: SLF001
    assert instance._state.parked_reason == "TEMPLATE_REVIEW_UNANSWERED"  # noqa: SLF001


@_async
async def test_an_approved_request_is_sent_while_another_is_still_being_read(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """**The map-based wait's whole reason for existing.**

    Two requests, one reviewer. They answer the second and never touch the
    first. The second must go out.

    A per-request gate -- `wait_condition` on request A, then on request B --
    would hold B's approved message until somebody dealt with A, and the
    reviewer would watch a message they approved sit there. That is precisely
    the shape the contract forbids ("not a per-request gate -- a held record
    must not block an approved one"), and it is invisible with one request,
    which is why this test exists rather than a comment.

    The wait still runs to its deadline afterwards, because request A is
    genuinely unanswered -- the point is that B's send did not wait for it.
    """
    second = f"{REQUEST_ID}:B"
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    activities = _GateActivities(gate, request_ids=(REQUEST_ID, second))
    holder: list[ReturnCaseWorkflow] = []
    approved: list[str] = []

    async def approve_the_second() -> None:
        review_id = holder[0]._state.template_reviews[second]  # noqa: SLF001
        await _approve(store, gate, review_id)
        approved.append(review_id)
        holder[0].template_approved(_notice(review_id, request_id=second))

    instance, _runtime = await _run_gate(
        monkeypatch,
        activities,
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
        holder=holder,
        arrivals=[approve_the_second],
    )

    assert len(support.posted) == 1, "the approved request went out while the other was still open"
    assert states_of_sent(await store.list_reviews(CASE_ID)) == {approved[0]}

    states = instance._state.template_review_states  # noqa: SLF001
    assert len(states) == 2, "the fixture must genuinely produce two requests"
    # The first was never answered and the wait ended on the deadline, so the
    # close parked it (AMENDMENT-5 rule 2). The point of this test is unchanged
    # and is the other assertion: the approved request went out **while** this
    # one was unanswered.
    assert states[REQUEST_ID] == ReviewState.HELD_FOR_OPERATIONS.value
    assert instance._state.parked_reason == "TEMPLATE_REVIEW_UNANSWERED"  # noqa: SLF001


@_async
async def test_a_notice_naming_another_review_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """A decision about a review this case is not waiting on.

    A superseded attempt after a redraft, or a notice that reached the wrong
    workflow id. Acting on it would send a message on the strength of an
    approval of something else -- which is the multi-RMA failure in its
    single-record clothes.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []

    def approve_the_wrong_one() -> None:
        holder[0].template_approved(_notice("review-from-another-case"))

    instance, _runtime = await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
        holder=holder,
        arrivals=[approve_the_wrong_one],
    )

    assert support.posted == []
    # The wait ran to its deadline rather than being ended by the stray notice.
    assert instance._state.parked_reason == "TEMPLATE_REVIEW_UNANSWERED"  # noqa: SLF001


@pytest.fixture
def signalling(monkeypatch: pytest.MonkeyPatch) -> ReturnCaseWorkflow:
    """A workflow instance whose signal handlers can log.

    `workflow.logger` is a real Temporal sandbox logger that refuses to answer
    outside a workflow event loop, so a handler test that did not substitute
    the module would fail on the log line rather than on the rule.
    """
    monkeypatch.setattr(workflow_module, "workflow", _Runtime({}))
    return ReturnCaseWorkflow()


def test_a_redelivered_signal_is_applied_once(signalling: ReturnCaseWorkflow) -> None:
    """The transport is at-least-once, so the second delivery *will* happen.

    Deduped on `signal_id` and **not** on `review_id`: a redraft mints a new
    attempt under the same request, and a handler keyed on the review would
    refuse the second attempt's genuine decision as a redelivery of the first's.
    """
    notice = _notice("review-1")
    signalling.template_approved(notice)
    signalling.template_approved(notice)

    assert len(signalling._state.pending_template_notices) == 1  # noqa: SLF001

    # The rule that keeps it from being "one decision per review".
    signalling.template_approved(_notice("review-1", signal_id="sig-second-attempt"))

    assert len(signalling._state.pending_template_notices) == 2  # noqa: SLF001


def test_an_unkeyed_signal_is_refused(signalling: ReturnCaseWorkflow) -> None:
    """No sender predates this field, so there is no first-wins fallback to
    grant -- and granting one would make a redelivery indistinguishable from a
    second reviewer."""
    signalling.template_approved(_notice("review-1", signal_id=""))

    assert signalling._state.pending_template_notices == []  # noqa: SLF001


def test_every_gate_signal_shares_the_one_dedupe(signalling: ReturnCaseWorkflow) -> None:
    """Approve, revise and cancel all go through `_accept_template_signal`.

    Three handlers with three dedupe rules is how one of them comes to have
    none, and the one that would be missed is whichever is exercised least.
    """
    for send in (
        signalling.template_approved,
        signalling.template_revised,
        signalling.template_cancelled,
    ):
        send(_notice("review-1", signal_id="shared-id"))

    assert len(signalling._state.pending_template_notices) == 1  # noqa: SLF001


def test_the_clarification_signal_is_accepted_and_acted_on_nowhere(
    signalling: ReturnCaseWorkflow,
) -> None:
    """V3's, declared and not implemented (brief item 3).

    Recorded so the answer is not lost, and written by nothing here: writing it
    is `record_clarification_answer`, and a second implementation would be a
    second record of one answer.
    """
    from return_platform.workflows.return_case_workflow import (
        _V3_CLARIFICATION_ACTIVITY,
        ClarificationAnsweredNotice,
    )

    signalling.clarification_answered(
        ClarificationAnsweredNotice(
            clarification_id="c-1", actor="associate-a", signal_id="sig-c-1", answer="yes"
        )
    )

    assert len(signalling._state.clarification_answers) == 1  # noqa: SLF001
    assert signalling._state.pending_template_notices == []  # noqa: SLF001
    assert _V3_CLARIFICATION_ACTIVITY == "record_clarification_answer"


# --------------------------------------------------------------------------- #
# The wait survives a history reset
# --------------------------------------------------------------------------- #


@_async
async def test_a_reset_mid_wait_carries_the_deadline_and_the_map(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """`continue_as_new` inside the gate must not restart the reviewer's clock.

    Recomputing the deadline on the far side would grant a fresh full wait on
    every history reset -- and losing the map would re-open a review the
    associate is already reading, replacing their draft with an identical one
    they have to read again.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    with pytest.raises(_ContinueAsNew) as raised:
        await _run_gate(
            monkeypatch,
            _GateActivities(gate),
            _timings(configuration, review_wait_seconds=600, reminder_interval_seconds=60),
            suggest_continue_as_new=True,
        )

    carried = raised.value.workflow_input
    assert carried.resumed_template_review_deadline_iso is not None
    assert carried.resumed_template_reviews, "the map must survive the reset"
    assert carried.template_review_reminders_sent >= 1
    assert carried.resumed_status == ReturnCaseStatus.AWAITING_TEMPLATE_REVIEW.value


@_async
async def test_the_far_side_of_a_reset_reuses_the_review_it_was_reading(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The other half. A second attempt over one request would be two answers
    to one question, and the reviewer would be shown the second."""
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    activities = _GateActivities(gate)
    timings = _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60)

    with pytest.raises(_ContinueAsNew) as raised:
        await _run_gate(monkeypatch, activities, timings, suggest_continue_as_new=True)
    carried = raised.value.workflow_input
    first_reviews = await store.list_reviews(CASE_ID)

    await _run_gate(monkeypatch, activities, timings, workflow_input=carried)

    assert len(await store.list_reviews(CASE_ID)) == len(first_reviews) == 1


def test_a_reset_outside_the_gate_is_not_mistaken_for_one_inside_it(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Both halves of `_resumed_into_the_review_gate` are load-bearing.

    The status alone is true of a case whose reviews all settled before the
    reset; the map alone is true after the gate finished. Only together do they
    mean "reviews were opened and the wait had not ended" -- and getting this
    wrong sends a case back through the policy evaluator, or skips it.
    """
    timings = _timings(configuration)
    guard = ReturnCaseWorkflow._resumed_into_the_review_gate  # noqa: SLF001

    assert guard(_input(timings)) is False
    assert (
        guard(_input(timings, resumed_status=ReturnCaseStatus.AWAITING_TEMPLATE_REVIEW.value))
        is False
    ), "status without a map is a gate that finished"
    assert guard(_input(timings, resumed_template_reviews=(("r", "v"),))) is False, (
        "a map without the status is a gate that finished"
    )
    assert (
        guard(
            _input(
                timings,
                resumed_status=ReturnCaseStatus.AWAITING_TEMPLATE_REVIEW.value,
                resumed_template_reviews=(("r", "v"),),
            )
        )
        is True
    )


# --------------------------------------------------------------------------- #
# The release actually reaches the workflow
# --------------------------------------------------------------------------- #


def test_the_shipped_release_reaches_the_pinned_timings(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The wiring test, one layer below condition 3's.

    Every gate decision in this file reads `ReturnCaseTimings`. If the launcher
    stopped copying `support_gate` onto it, every test above would keep passing
    against the dataclass defaults -- which happen to equal the shipped block --
    while a deployment that changed the block got the defaults instead. So the
    values are compared against a block that is deliberately *not* the default.
    """
    block = SupportGateConfiguration(
        template_review=TemplateReviewConfiguration(
            enabled=False,
            review_wait_seconds=999,
            reminder_interval_seconds=111,
            max_reminders=7,
            on_timeout=TemplateReviewTimeoutPolicy.AUTO_SEND,
        )
    )
    timings = case_timings_from_configuration(configuration.return_case, block)

    assert timings.template_review_enabled is False
    assert timings.template_review_wait_seconds == 999
    assert timings.template_review_reminder_interval_seconds == 111
    assert timings.template_review_max_reminders == 7
    assert timings.template_review_on_timeout == "auto_send"


def test_omitting_the_block_gives_the_reviewed_default_not_no_gate() -> None:
    """DR-4 at the wiring layer. A caller that forgets must get the review."""
    from return_platform.configuration.return_configuration import (
        ReturnCaseTimingConfiguration,
    )

    timings = case_timings_from_configuration(ReturnCaseTimingConfiguration())

    assert timings.template_review_enabled is True
    assert timings.template_review_on_timeout == "hold"


# --------------------------------------------------------------------------- #
# AMENDMENT-5 rule 2: no review is left in a state with no legal exit
# --------------------------------------------------------------------------- #


@_async
async def test_a_gate_closing_over_a_failed_delivery_parks_it_with_an_exit(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The half that makes the guarantee rather than the refusal.

    Rule 1 stops a retry stranding a review; on its own it leaves the operator
    with nothing but a 409. This asserts the **property**, not the transition:
    after the gate closes, the review is in a state that has a legal exit, and
    the exits are reachable by the API rather than only by the workflow.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)
    holder: list[ReturnCaseWorkflow] = []

    async def approve_then_fail_the_send() -> None:
        review_id = next(iter(holder[0]._state.template_reviews.values()))  # noqa: SLF001
        await _approve(store, gate, review_id)
        await store.mark_delivery_failed(
            case_id=CASE_ID, review_id=review_id, error_code="SUPPORT_UNREACHABLE"
        )

    await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
        holder=holder,
        arrivals=[approve_then_fail_the_send],
    )

    review = (await store.list_reviews(CASE_ID))[0]
    state = ReviewState(str(review["state"]))
    assert state is ReviewState.HELD_FOR_OPERATIONS
    assert review["holdReason"] == TemplateReviewParkReason.TEMPLATE_REVIEW_UNANSWERED.value

    # **The absence of the stranded state, asserted directly.** A review the
    # gate has stopped holding must not be in any state whose exits are all
    # workflow-driven -- which is what `APPROVING` is, and what the endpoint
    # used to be able to put it into.
    assert state is not ReviewState.APPROVING
    assert state not in {ReviewState.OPEN, ReviewState.DELIVERY_FAILED}

    # And both exits actually work from here, which is the point of choosing
    # this state over any other.
    reopened = await store.resume_from_hold(
        case_id=CASE_ID, review_id=str(review["_id"]), actor_id="operator-a"
    )
    assert ReviewState(str(reopened["state"])) is ReviewState.OPEN
    held_again = await store.hold_for_operations(
        case_id=CASE_ID,
        review_id=str(review["_id"]),
        reason=TemplateReviewParkReason.TEMPLATE_REVIEW_UNANSWERED,
    )
    assert ReviewState(str(held_again["state"])) is ReviewState.HELD_FOR_OPERATIONS
    abandoned = await store.abandon(
        case_id=CASE_ID,
        review_id=str(review["_id"]),
        actor_id="operator-a",
        reason="support resolved it on the phone",
    )
    assert ReviewState(str(abandoned["state"])) is ReviewState.ABANDONED


@_async
async def test_every_state_the_gate_can_close_over_ends_with_a_legal_exit(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The amendment says *every* non-terminal review, and this checks the word.

    A test that only covered `DELIVERY_FAILED` would miss the `OPEN` case, which
    is the one that is not obvious: an `OPEN` review after the gate has closed
    is the same trap through the **approve** endpoint, because approving CASes
    it to `APPROVING` and the workflow discards the notice.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)

    # Closed with the review untouched, so it is `OPEN` on the way out.
    await _run_gate(
        monkeypatch,
        _GateActivities(gate),
        _timings(configuration, review_wait_seconds=120, reminder_interval_seconds=60),
    )

    review = (await store.list_reviews(CASE_ID))[0]
    assert ReviewState(str(review["state"])) is ReviewState.HELD_FOR_OPERATIONS, (
        "an OPEN review left behind by a closed gate can still be approved into "
        "a state the workflow will never settle"
    )


@_async
async def test_a_continue_as_new_is_not_a_close(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The one exit that must **not** park anything.

    A `continue_as_new` unwinds this method and the next run re-enters it,
    holding the same reviews. Parking on the way out would settle the gate
    against itself: `HELD_FOR_OPERATIONS` is a resolved state, so the resumed
    run would find every review settled and send nothing -- for a case nobody
    had answered.
    """
    support = _Support()
    gate = _gate_service(store, mongo, test_settings, configuration, support)

    with pytest.raises(_ContinueAsNew):
        await _run_gate(
            monkeypatch,
            _GateActivities(gate),
            _timings(configuration, review_wait_seconds=600, reminder_interval_seconds=60),
            suggest_continue_as_new=True,
        )

    review = (await store.list_reviews(CASE_ID))[0]
    assert ReviewState(str(review["state"])) is ReviewState.OPEN, (
        "a resumed gate must still have something to wait for"
    )


def test_the_workflows_state_words_are_the_aggregates() -> None:
    """The workflow spells review states as **string literals**, on purpose.

    `return_case_workflow.py` imports nothing from `review_aggregate`, which
    keeps S2's module -- and everything it imports -- out of the Temporal
    workflow sandbox. The cost is that the state names live in two places, and
    this is the test that stops them drifting: a rename in S2's enum fails here
    rather than in a running gate that silently never settles.

    Both directions, because either alone is weak. Every word the workflow uses
    must be a real state, and every non-terminal-plus-resolved state the
    aggregate has must be accounted for -- a state S2 adds and the workflow has
    never heard of would make `_reviews_settled` wait for ever.
    """
    aggregate_words = {state.value for state in ReviewState}

    assert workflow_module._RESOLVED_REVIEW_STATES <= aggregate_words  # noqa: SLF001
    assert "HELD_FOR_OPERATIONS" in aggregate_words
    assert "OPEN" in aggregate_words
    assert "CANCELLED" in aggregate_words

    # The resolved set is exactly "not waiting on a human decision": every
    # terminal state, plus the two the gate stops holding.
    assert workflow_module._RESOLVED_REVIEW_STATES == {  # noqa: SLF001
        ReviewState.SENT.value,
        ReviewState.CANCELLED.value,
        ReviewState.ABANDONED.value,
        ReviewState.DELIVERY_FAILED.value,
        ReviewState.HELD_FOR_OPERATIONS.value,
    }
    assert aggregate_words - workflow_module._RESOLVED_REVIEW_STATES == {  # noqa: SLF001
        ReviewState.OPEN.value,
        ReviewState.APPROVING.value,
    }, "the two states the gate genuinely waits on"
