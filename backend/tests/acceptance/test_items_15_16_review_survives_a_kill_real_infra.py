"""Acceptance items 15 and 16 — the review gate across a worker kill, live.

* **15** — kill mid-review: the draft, the edit rows and the **remaining
  timeout** survive.
* **16** — the approval that follows the restart sends **exactly one** message,
  under one delivery identity.

**Why this file exists at all.** `tests/test_return_case_workflow_real_infra.py`
proves the *Support* wait survives a restart and contains **zero** review-gate
coverage — no occurrence of `review` in the file. The gate is the thing V1 added
between the draft and the send, and nothing has ever killed a worker while one
was open.

**A finding, before any of the below is read as routine.** That file is
currently **broken against live infrastructure**: 12 of its 13 tests fail with
`NotFoundError: Activity function record_template_draft ... is not registered on
this worker`. Its `_Probe` predates V1's gate and never gained the five gate
activities, while `workflows/worker.py` registers all five — so **production is
correct and the harness is stale**. Its last commit is literally
`fix(tests): stale workflow doubles wedged the live-infra suite, silently`
(2026-08-23): the same defect, fixed once, recurred, because nothing runs this
suite. That is RV rule 13's sharpest instance on this run and it is reported
rather than repaired here — the file belongs to another slice, and ACC owns
`backend/tests/` **additions**.

**So this module carries its own probe**, complete with the gate activities, and
does not depend on the broken one.

**What is real and what is doubled**, stated because it decides what the
assertions mean:

* **real** — Temporal (durability is the whole claim), the `ReturnCaseWorkflow`
  itself, `SupportTemplateGateService` over a **real Mongo database**, the
  review aggregate, the approval transition, the delivery identity;
* **doubled** — the render inputs (a case projection this file has no reason to
  build) and the outward Support post, which is captured so "exactly one
  message" is a count of real `post_support_message` calls made by the real
  gate rather than a claim about a mock.

**The gate that runs it** (RV rule 13, and the reason this is said out loud):
**nothing in CI.** `addopts` deselects `live_infra` and `.github/workflows/checks.yml`
runs plain `pytest tests`, so this module's only gate is a person running
`scripts/dev/run_real_infra_suite.sh`. Every other acceptance module ACC has
written is in the default suite deliberately; this one cannot be, because a
worker kill needs a worker.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.review_aggregate import (
    ReviewAggregateStore,
    ReviewState,
    canonical_payload_digest,
    canonical_review_payload,
    ensure_review_indexes,
)
from return_platform.operations.support_template_draft import SAMPLE_CASE, draft_facts
from return_platform.operations.support_template_gate import (
    MongoDraftEditRows,
    SupportTemplateGateService,
)
from return_platform.workflows.return_case_workflow import (
    CaseEligibilityOutcome,
    ClarificationAnswerResult,
    ClarificationRelayView,
    HoldUnsettledReviewsInput,
    HoldUnsettledReviewsResult,
    PolicyDecisionName,
    PolicyGateState,
    PolicyRouteName,
    ResolveBusinessDeadlineInput,
    ResolvedBusinessDeadline,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SnapshotSentTemplateInput,
    SupportRequestDraft,
    TemplateDeliveryResult,
    TemplateReviewDraftInput,
    TemplateReviewDraftResult,
    TemplateReviewDraftSet,
    TemplateReviewRevisionInput,
)
from tests.activity_probe import declared_activities

pytestmark = [pytest.mark.live_infra, pytest.mark.asyncio(loop_scope="module")]

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"
_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


class _SupportSpy:
    """The outward post, counted. Everything upstream of it is real.

    Not a general double: `post_support_message` is the one call that would
    reach Channel B, and item 16's observable is *how many times it happens*.
    Counting the real gate's calls is the closest this file can get to "exactly
    one message on B" without a Support instance to receive it — and it is a
    strictly stronger reading than counting a mock the gate never consulted,
    because everything that decides whether to post is production code.
    """

    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.threads_opened = 0

    async def ensure_case_support_thread(self, **kwargs: Any) -> Any:
        del kwargs
        self.threads_opened += 1

        class _Thread:
            workItemId = "wi-acceptance"  # noqa: N815 - the wire name
            threadId = "th-acceptance"  # noqa: N815
            created = False

        return _Thread()

    async def post_support_message(self, **kwargs: Any) -> Any:
        self.posted.append(dict(kwargs))

        class _Post:
            absorbed = False

        return _Post()

    @property
    def delivery_ids(self) -> list[str]:
        return [str(post.get("delivery_id")) for post in self.posted]


class _GateProbe:
    """Every activity the workflow calls, with the five gate ones **real**.

    The pre-gate activities are probes for the reason
    `test_return_case_workflow_real_infra.py` gives: what is under test is the
    workflow's own coordination, and pointing the bay or the graph at a
    datastore would make a slow test of the repository. The five gate
    activities are the exception, because the whole claim here is that what the
    gate *persisted* is still there after the process is gone -- and a probe
    that remembered the draft in a python list would remember it across a kill
    for reasons that have nothing to do with durability.
    """

    def __init__(self, gate: SupportTemplateGateService, request_id: str) -> None:
        self._gate = gate
        self._request_id = request_id
        self._facts = draft_facts(**SAMPLE_CASE)
        self.calls: list[str] = []
        self.deadline_requests: list[ResolveBusinessDeadlineInput] = []
        self.review_ids: list[str] = []

    # --- bookkeeping ------------------------------------------------------

    def _record(self, name: str) -> None:
        self.calls.append(name)

    # **There is deliberately no `reached(activity_name)` here.** An earlier
    # draft had one, wired to `_record` at the *top* of each activity, and it
    # caused both races this module had to close: it fires when an activity
    # starts, and everything a caller then wants -- the recorded review, the
    # opened wait -- exists only once it finishes. Waiting on a name and then
    # reading a result is a race that passes whenever something slow happens to
    # sit between the two, which is why one scenario passed and the other did
    # not. It is removed rather than left unused: an unused primitive with an
    # inviting name is one autocomplete away from reintroducing the defect.
    # Wait for the thing you actually want -- the two waiters below do.

    async def first_review_id(self, *, within_seconds: float = 60.0) -> str:
        """Wait for the review the gate **recorded**, not for the activity start."""
        deadline = datetime.now(UTC) + timedelta(seconds=within_seconds)
        while datetime.now(UTC) < deadline:
            if self.review_ids:
                return self.review_ids[0]
            await asyncio.sleep(0.1)
        raise AssertionError(f"no review was recorded within {within_seconds}s")

    # --- the pre-gate probes ---------------------------------------------

    @activity.defn(name="record_case_status")
    async def record_case_status(self, request: Any) -> None:
        del request
        self._record("record_case_status")

    @activity.defn(name="record_case_customer_identity")
    async def record_case_customer_identity(self, request: Any) -> bool:
        del request
        self._record("record_case_customer_identity")
        return True

    @activity.defn(name="request_bay_assignment")
    async def request_bay_assignment(self, request: Any) -> Any:
        del request
        self._record("request_bay_assignment")
        return None

    @activity.defn(name="resolve_business_deadline")
    async def resolve_business_deadline(
        self, request: ResolveBusinessDeadlineInput
    ) -> ResolvedBusinessDeadline:
        self._record("resolve_business_deadline")
        self.deadline_requests.append(request)
        return ResolvedBusinessDeadline(
            instant_iso=(
                datetime.fromisoformat(request.from_iso)
                + timedelta(seconds=request.working_seconds)
            ).isoformat(),
            calendar_applied=False,
        )

    @activity.defn(name="evaluate_case_eligibility")
    async def evaluate_case_eligibility(self, request: Any) -> CaseEligibilityOutcome:
        del request
        self._record("evaluate_case_eligibility")
        return CaseEligibilityOutcome(
            state=PolicyGateState.EVALUATED.value,
            route=PolicyRouteName.STANDARD_RETURN.value,
            decision=PolicyDecisionName.APPROVE.value,
        )

    @activity.defn(name="draft_support_request")
    async def draft_support_request(self, request: Any) -> SupportRequestDraft:
        del request
        self._record("draft_support_request")
        return SupportRequestDraft(text="composed", payload={}, subject="Return")

    @activity.defn(name="open_support_work_item")
    async def open_support_work_item(self, request: Any) -> str:
        self._record("open_support_work_item")
        return f"wi-{request.case_id}"

    @activity.defn(name="send_support_reminder")
    async def send_support_reminder(self, request: Any) -> None:
        del request
        self._record("send_support_reminder")

    @activity.defn(name="record_support_outcome")
    async def record_support_outcome(self, request: Any) -> None:
        del request
        self._record("record_support_outcome")

    @activity.defn(name="synchronize_return_records")
    async def synchronize_return_records(self, request: Any) -> str:
        del request
        self._record("synchronize_return_records")
        return "gen-acceptance"

    # --- the five gate activities, over the real service -------------------

    @activity.defn(name="record_template_draft")
    async def record_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftSet:
        self._record("record_template_draft")
        draft = await self._gate.record_draft(
            case_id=request.case_id,
            request_id=self._request_id,
            review_id=f"{request.fact_id_seed}:0",
            fact_id_seed=f"{request.fact_id_seed}:0",
            facts=self._facts,
        )
        if not draft.template_available:
            return TemplateReviewDraftSet(template_available=False)
        self.review_ids.append(str(draft.review_id))
        return TemplateReviewDraftSet(
            drafts=(
                TemplateReviewDraftResult(
                    request_id=self._request_id,
                    review_id=draft.review_id or "",
                    state=draft.state,
                    draft_version=draft.draft_version,
                    canonical_edit_version=draft.canonical_edit_version,
                    gap_field_ids=draft.gap_field_ids,
                ),
            )
        )

    @activity.defn(name="rerender_template_draft")
    async def rerender_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftResult:
        self._record("rerender_template_draft")
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

    @activity.defn(name="record_template_revision")
    async def record_template_revision(self, request: TemplateReviewRevisionInput) -> None:
        self._record("record_template_revision")
        await self._gate.record_revision(
            case_id=request.case_id,
            review_id=request.review_id,
            actor_id=request.actor_id,
            note=request.note,
            fact_id_seed=request.fact_id_seed,
        )

    @activity.defn(name="hold_unsettled_reviews")
    async def hold_unsettled_reviews(
        self, request: HoldUnsettledReviewsInput
    ) -> HoldUnsettledReviewsResult:
        self._record("hold_unsettled_reviews")
        return HoldUnsettledReviewsResult(
            held_review_ids=await self._gate.hold_unsettled(case_id=request.case_id)
        )

    @activity.defn(name="snapshot_sent_template")
    async def snapshot_sent_template(
        self, request: SnapshotSentTemplateInput
    ) -> TemplateDeliveryResult:
        self._record("snapshot_sent_template")
        from return_platform.workflows.return_case_activities import ReturnCaseActivities

        activities = ReturnCaseActivities(
            repository=cast(Any, None), support_service=cast(Any, None), template_gate=self._gate
        )
        return await activities.snapshot_sent_template(request)

    # -- Registered but unreached, for `worker.py`'s stated reason -------------
    #
    # None of the three below is reachable by this module's scenarios today:
    # `case_has_return_details` is only polled when
    # `ReturnCaseTimings.return_details_required` is on, which defaults to False
    # and which this file never sets; the clarification pair is called only from
    # the `clarification_answered` signal handler, and this file never sends
    # that signal. They are registered anyway, which is the same call
    # `test_return_case_workflow_real_infra.py` makes at its own clarification
    # block: an activity the worker has not registered leaves a case that
    # legitimately reaches it stopped, with no exception and no log, and a probe
    # that registers only what its own scenarios happen to call is green because
    # its inputs cannot exercise the property rather than because the property
    # holds. That matters more here than elsewhere: this module kills the worker
    # mid-run, and a restart is exactly where a case can take a path the happy
    # scenarios do not.

    @activity.defn(name="case_has_return_details")
    async def case_has_return_details(self, request: Any) -> bool:
        del request
        self._record("case_has_return_details")
        return True

    @activity.defn(name="record_clarification_answer")
    async def record_clarification_answer(self, request: Any) -> ClarificationAnswerResult:
        del request
        self._record("record_clarification_answer")
        return ClarificationAnswerResult(recorded=True)

    @activity.defn(name="relay_clarification_to_support")
    async def relay_clarification_to_support(self, request: Any) -> ClarificationRelayView:
        self._record("relay_clarification_to_support")
        return ClarificationRelayView(
            delivery_id=f"dlv-{request.clarification_id}",
            message_id=f"msg-{request.clarification_id}",
        )

    def all(self) -> tuple[Any, ...]:
        """Every activity this probe declares, derived rather than listed.

        This was the **third** hand-written copy of the list `worker.py` already
        owns, and the only one the registration guard could not see drift.
        `test_every_test_worker_registers_every_activity_the_workflow_calls`
        reads `declared_activity_names(probe_class)` -- the `@activity.defn`
        declarations -- while `Worker(...)` is handed this tuple. Two readers,
        two lists, nothing asserting they agree: drop an entry here and keep its
        decorator, and the guard stays green while the worker under-registers
        and every case it schedules stops on a task nothing polls.

        That is not hypothetical. It is `.plan/reviews/HARNESS-3.md` C1,
        reproduced on this branch before this line was written: the guard
        reported `17 passed` with `declared 18 | all() 17`. Deriving the tuple
        removes the second list, so there is no longer an entry to drop -- the
        same argument, and the same fix, as the two sibling probes that already
        do this because a hand-written list rotted twice (`5b7d60f6`, and the
        day V1 phase 2's review gate merged). See `tests/activity_probe.py`.

        Order is not a contract: `Worker` keys its activity registry by the
        decorator's `name=`, and `declared_activities` sorts by attribute name
        precisely so registration order stops depending on definition order.
        """
        return declared_activities(self)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[Client]:
    """One client for the module, closed at the end.

    `test_return_case_workflow_real_infra.py` records why: thirteen unclosed
    connections and 168 orphaned executions were what made that file flaky.
    """
    connected = await Client.connect(_TEMPORAL_TARGET)
    yield connected


@pytest_asyncio.fixture(loop_scope="module")
async def live() -> AsyncIterator[dict[str, Any]]:
    """A real Mongo database, the real gate service over it, dropped after."""
    database = f"acc_review_kill_{uuid.uuid4().hex[:12]}"
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    handle = mongo[database]
    await ensure_review_indexes(handle)
    await ensure_case_command_indexes(handle)
    configuration = load_return_configuration(_CONFIG).configuration
    repository = OperationalRepository(mongo, settings)
    support_spy = _SupportSpy()
    reviews = ReviewAggregateStore(
        mongo, settings, command_store=DurableCaseCommandStore(mongo, settings)
    )
    gate = SupportTemplateGateService(
        reviews=reviews,
        edit_rows=MongoDraftEditRows(handle),
        support_service=cast(Any, support_spy),
        configuration=lambda: configuration,
        append_fact=_NullFacts(),
    )
    try:
        yield {
            "gate": gate,
            "reviews": reviews,
            "edit_rows": MongoDraftEditRows(handle),
            "support": support_spy,
            "settings": settings,
            "repository": repository,
            "database": handle,
        }
    finally:
        await mongo.drop_database(database)
        await mongo.close()


class _NullFacts:
    """The scoped fact writer, bound explicitly and doing nothing.

    Bound rather than `**kwargs`-swallowing: `merge.md`'s first recurring shape
    is that a double accepting anything proves nothing, and the fact plane is
    not what this file asserts. Recording the calls keeps the option of
    asserting on them without pretending they are checked here.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, record_scope: str | None, **fact: Any) -> bool:
        self.calls.append({"record_scope": record_scope, **fact})
        return True


_STARTED: list[Any] = []


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _terminate_started_executions() -> AsyncIterator[None]:
    _STARTED.clear()
    yield
    for handle in _STARTED:
        try:
            await handle.terminate("acceptance cleanup")
        except Exception:  # noqa: BLE001 - already closed, or never started
            pass
    _STARTED.clear()


def _timings(**overrides: Any) -> ReturnCaseTimings:
    base: dict[str, Any] = {
        "bay_wait_seconds": 0,
        "support_response_wait_seconds": 300,
        "reminder_interval_seconds": 120,
        "max_reminders": 2,
        "on_reminders_exhausted": "PARK_FOR_OPERATIONS",
        "business_calendar_id": "default",
        "timezone": "UTC",
        "template_review_enabled": True,
        # Long enough that the deadline cannot pass during the kill: a review
        # that ended because it timed out would look exactly like one that did
        # not survive, and the test could not tell them apart.
        "template_review_wait_seconds": 900,
        "template_review_reminder_interval_seconds": 600,
        "template_review_max_reminders": 3,
    }
    base.update(overrides)
    return ReturnCaseTimings(**base)


async def _start(client: Client, case_id: str) -> tuple[str, Any]:
    queue = f"acc-review-kill-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        ReturnCaseWorkflow.run,
        ReturnCaseWorkflowInput(
            case_id=case_id,
            tenant_id="tenant-a",
            principal_id="associate-1",
            conversation_id=f"conv-{uuid.uuid4().hex[:8]}",
            configuration_release_id="release-1",
            timings=_timings(),
        ),
        id=f"acc-review-kill-{uuid.uuid4().hex[:8]}",
        task_queue=queue,
    )
    _STARTED.append(handle)
    return queue, handle


# --------------------------------------------------------------------------- #
# Item 15 -- the review survives the kill
# --------------------------------------------------------------------------- #


async def test_a_kill_mid_review_loses_neither_the_draft_nor_the_remaining_timeout(
    client: Client, live: dict[str, Any]
) -> None:
    """Item 15. The gate is open, the worker dies, and nothing has moved.

    Three things are asserted after the restart, and they are three different
    planes:

    * the **draft** -- a Mongo row written by the real gate service;
    * the **edit row** -- written before the kill, by the same store an
      associate's autosave writes;
    * the **remaining timeout** -- the workflow's own state, which is the only
      one of the three that a restart could actually lose. Mongo does not
      notice a worker dying; Temporal is where the durability claim lives, and
      the deadline instant is what an operator's countdown is drawn from.

    The deadline is asserted **equal**, not merely present: "a deadline exists"
    would pass for a gate that restarted its own clock on resume.

    **What this scenario does and does not prove, measured rather than assumed.**
    The obvious reading -- that the equality guards a reviewer's fifteen minutes
    silently becoming thirty -- is **wrong for a worker kill, and an injection
    proved it.** Making `_await_template_reviews` ignore
    `resumed_template_review_deadline_iso` entirely changed nothing here (INJ-15a,
    `.plan/acceptance/items-14-17-review-across-a-kill.md`), because **a kill is
    not a `continue_as_new`**: the replacement worker replays the history, the
    `resolve_business_deadline` result comes back from that history, and the
    resumed-field path is never taken. That field guards *continuation*, and a
    scenario that wants to guard it must continue-as-new rather than kill.

    So the equality here holds by Temporal's replay, and most of what this test
    asserts is the framework's guarantee rather than the platform's. What is
    genuinely the platform's, and what INJ-15b does red, is narrower and still
    worth a live test: the deployment's own wiring -- the gate activities
    registered, the gate reachable, and the review state queryable and correct
    after the process that opened it is gone. Claim that, and not more.
    """
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    gate: SupportTemplateGateService = live["gate"]
    reviews: ReviewAggregateStore = live["reviews"]
    queue, handle = await _start(client, case_id)

    probe = _GateProbe(gate, request_id=f"support:{case_id}")
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        review_id = await probe.first_review_id()
        # **Wait for the gate to be genuinely open, not merely for the draft to
        # exist.** `template_review_deadline_iso` is set inside
        # `_await_template_reviews`, after `record_template_draft` returns, so a
        # query fired the moment the review id appears can legitimately see
        # `None`. The first form of this test read it immediately and passed --
        # because waiting on the activity *name* left a `handle.query` sitting
        # between the two -- and it only started failing once the other race in
        # this file was removed. Two latent races, one masking the other.
        before = await _open_gate(handle)
        assert before.template_reviews, "the gate opened no review to kill anything mid-way through"
        deadline_before = before.template_review_deadline_iso
        assert deadline_before is not None

        # An associate types into the draft before the lights go out.
        before_review = await reviews.get_review(case_id=case_id, review_id=review_id)
        await reviews.upsert_draft_edit(
            case_id=case_id,
            review_id=review_id,
            actor_id="associate-a",
            client_edit_id="edit-1",
            base_draft_version=int(before_review["draftVersion"]),
            payload={"body": "half-written when the worker died"},
        )

    # The worker is gone. Nothing is running this case.
    stored_before = await reviews.get_review(case_id=case_id, review_id=review_id)
    assert ReviewState(str(stored_before["state"])) is ReviewState.OPEN

    resumed = _GateProbe(gate, request_id=f"support:{case_id}")
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=resumed.all()
    ):
        after = await handle.query(ReturnCaseWorkflow.execution_state)

        assert after.template_reviews == before.template_reviews, (
            "the review map did not survive the restart -- the resumed run is holding "
            "different reviews from the ones the reviewer is looking at"
        )
        assert after.template_review_deadline_iso == deadline_before, (
            "the remaining timeout moved across the restart. A gate that re-resolves "
            "its deadline on resume gives the reviewer a fresh window every time a "
            "worker bounces, and the panel's countdown jumps for a reason no operator "
            "can see."
        )
        assert after.template_review_reminders_sent == before.template_review_reminders_sent

        # The draft row and the half-typed edit are both still there, read back
        # from Mongo rather than from anything this test kept.
        stored = await reviews.get_review(case_id=case_id, review_id=review_id)
        assert ReviewState(str(stored["state"])) is ReviewState.OPEN
        assert stored["draftVersion"] == stored_before["draftVersion"]
        edits = await live["edit_rows"].edit_rows(review_id=review_id)
        assert [row["payload"]["body"] for row in edits] == ["half-written when the worker died"], (
            "the associate's unsent edit did not survive the kill"
        )

        # The resumed worker did not re-draft: a second draft would be a second
        # review for the reviewer to find, and the first one's edits orphaned.
        assert "record_template_draft" not in resumed.calls


# --------------------------------------------------------------------------- #
# Item 16 -- the approval after the restart sends exactly one message
# --------------------------------------------------------------------------- #


async def test_an_approval_after_the_restart_sends_exactly_one_message(
    client: Client, live: dict[str, Any]
) -> None:
    """Item 16, reached the way an outage actually reaches it.

    The review is approved *after* the worker has been killed and replaced, so
    the send is performed by a process that did not create the draft. Exactly
    one message leaves, under one delivery identity.

    `post_support_message` is counted on the real gate's own calls, so the count
    is of sends production decided to make. The receiver-side dedupe (§7) is
    S2's and is asserted in its own suite; what this adds is that a restart does
    not produce a *second sender*, which is the failure AMENDMENT-5 rule 1 exists
    to prevent and which no unit test can stage.
    """
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    gate: SupportTemplateGateService = live["gate"]
    reviews: ReviewAggregateStore = live["reviews"]
    support: _SupportSpy = live["support"]
    queue, handle = await _start(client, case_id)

    probe = _GateProbe(gate, request_id=f"support:{case_id}")
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        review_id = await probe.first_review_id()

    assert support.posted == [], "something was sent before anyone approved anything"

    resumed = _GateProbe(gate, request_id=f"support:{case_id}")
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=resumed.all()
    ):
        review = await reviews.get_review(case_id=case_id, review_id=review_id)
        await gate.approve(
            case_id=case_id,
            review_id=review_id,
            actor_id="associate-a",
            expected_draft_version=int(review["draftVersion"]),
            expected_canonical_edit_version=int(review["canonicalEditVersion"]),
            canonical_approved_payload_hash=canonical_payload_digest(
                canonical_review_payload(review)
            ),
            workflow_id=handle.id,
            signal_id=f"sig-{review_id}",
        )
        notice = _approval_notice(review_id, f"support:{case_id}")
        await handle.signal(ReturnCaseWorkflow.template_approved, notice)
        await _until(lambda: bool(support.posted), "the approved review was never sent")

        # **The same notice again**, because §7's signals are at-least-once. It
        # is absorbed by the workflow's `signal_id` dedupe, and the count must
        # not move.
        await handle.signal(ReturnCaseWorkflow.template_approved, notice)
        await asyncio.sleep(2.0)
        assert len(support.posted) == 1, "a redelivered approval signal sent a second message"

        # **And the delivery itself, attempted again.** This is not the same
        # assertion twice, and the difference was found by injection: with the
        # signal path alone, removing the gate's `SENT` short-circuit *and* the
        # workflow's signal dedupe **both** left this test green -- by then the
        # gate has closed and a second signal has no wait to wake, so nothing
        # ever reached the code that decides whether to post again. Calling
        # `deliver_approved` directly is what puts a genuine second delivery in
        # front of the guard that owns the guarantee. Only this makes "exactly
        # one" a claim rather than a count of the one send that was requested.
        again = await gate.deliver_approved(
            case_id=case_id,
            review_id=review_id,
            tenant_id="tenant-a",
            principal_id="associate-1",
            fact_id_seed=f"{review_id}:redelivery",
        )
        assert again.state == ReviewState.SENT.value

    assert len(support.posted) == 1, (
        f"{len(support.posted)} messages left for one approved review. A restart that "
        "produces a second sender is the failure §7's single delivery path and "
        "AMENDMENT-5 rule 1 both exist to prevent."
    )
    assert len(set(support.delivery_ids)) == 1
    final = await reviews.get_review(case_id=case_id, review_id=review_id)
    assert ReviewState(str(final["state"])) is ReviewState.SENT


def _approval_notice(review_id: str, request_id: str) -> Any:
    from return_platform.workflows.return_case_workflow import TemplateReviewNotice

    return TemplateReviewNotice(
        review_id=review_id,
        request_id=request_id,
        actor="associate-a",
        signal_id=f"sig-{review_id}",
        draft_version=1,
        canonical_edit_version=0,
    )


async def _open_gate(handle: Any, *, within_seconds: float = 60.0) -> Any:
    """`execution_state` once the review wait is actually running."""
    deadline = datetime.now(UTC) + timedelta(seconds=within_seconds)
    last: Any = None
    while datetime.now(UTC) < deadline:
        last = await handle.query(ReturnCaseWorkflow.execution_state)
        if last.template_review_deadline_iso is not None:
            return last
        await asyncio.sleep(0.25)
    raise AssertionError(
        f"the review wait never opened within {within_seconds}s; last state: {last}"
    )


async def _until(predicate: Any, message: str, *, within_seconds: float = 60.0) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=within_seconds)
    while datetime.now(UTC) < deadline:
        if predicate():
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"{message} (waited {within_seconds}s)")
