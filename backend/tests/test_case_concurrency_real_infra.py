"""CASE-01 and the audit's §29 concurrency matrix, at the persistence boundary.

Every claim in this module is settled against a real datastore. The unique
indexes, the compare-and-set filters and Temporal's execution-id uniqueness are
the mechanisms under test, and none of them exists in a double -- a fake store
that returns the second writer's document is exactly the test that let the
matrix stay unproven while looking covered.

**Genuinely concurrent, not merely repeated.** Each scenario releases its
writers from an `asyncio.Barrier`, so every task is inside the same window
rather than serialised by the order the test wrote them. A sequential retry
proves idempotency under retry; it does not prove it under a race, and the two
failure modes are different -- a check-then-write is idempotent under retry and
wrong under a race.

Scope, deliberately: this is the **persistence** boundary. Track B converted the
simultaneous confirmation at the graph layer with a gated fake server; that test
proves the agent's confirmation node calls the port once. This one proves that
when two confirmations reach real Mongo and real Temporal at the same instant,
exactly one case document and exactly one `ReturnCaseWorkflow` execution exist
afterwards -- contract C1, which no double can settle.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.order_agent.contracts import OrderConfirmation
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import (
    ConcurrencyConflictError,
    OperationalRepository,
)
from return_platform.workflows.return_case_launcher import TemporalCaseWorkflowLauncher
from return_platform.workflows.return_case_workflow import return_case_workflow_id

pytestmark = pytest.mark.asyncio

_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")

#: How many writers enter the window together.
#:
#: Two proves the race exists; eight makes a check-then-write that happens to
#: win twice in a row fail reliably. The cost is one extra round trip per writer
#: against infrastructure that is already up.
CONTENDERS = 8

TENANT_ID = "tenant-concurrency"
PRINCIPAL_ID = "associate-concurrency"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `tests/conftest.py::test_settings`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/return_platform"
        "?authSource=admin&directConnection=true"
    )


@pytest_asyncio.fixture
async def repository(test_settings: Settings) -> AsyncIterator[OperationalRepository]:
    """The real repository, with its real indexes.

    `ensure_indexes` is the point of the fixture. Every claim below is enforced
    by a unique or partial index, so a repository without them would let all
    eight writers succeed and the suite would report the opposite of the truth.
    """
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    instance = OperationalRepository(mongo, test_settings)
    await instance.ensure_indexes()
    try:
        yield instance
    finally:
        await mongo.close()


@pytest_asyncio.fixture
async def temporal() -> AsyncIterator[Client]:
    yield await Client.connect(_TEMPORAL_TARGET)


async def _purge(repository: OperationalRepository, case_ids: tuple[str, ...]) -> None:
    for case_id in case_ids:
        await repository.cases.delete_many({"caseId": case_id})
        await repository.case_facts.delete_many({"caseId": case_id})
        await repository.return_records.delete_many({"caseId": case_id})
        await repository.return_items.delete_many({"caseId": case_id})


async def _terminate(temporal: Client, case_ids: tuple[str, ...]) -> None:
    for case_id in case_ids:
        try:
            await temporal.get_workflow_handle(return_case_workflow_id(case_id)).terminate(
                "concurrency test cleanup"
            )
        except Exception:  # noqa: BLE001 - never started, or already gone
            pass


async def _released_together(count: int, work: Any) -> list[Any]:
    """Run `work(index)` `count` times, all released from one barrier.

    `asyncio.gather` alone does not produce a race: the first task runs until
    its first await, and against a fast local datastore that can be the whole
    operation. The barrier moves every task to the same starting line first, so
    the contended window is the operation itself.

    Exceptions are returned rather than raised -- for most of these scenarios
    the losers' exceptions *are* the assertion.
    """
    barrier = asyncio.Barrier(count)

    async def _run(index: int) -> Any:
        await barrier.wait()
        return await work(index)

    return await asyncio.gather(*(_run(index) for index in range(count)), return_exceptions=True)


# --- CASE-01: simultaneous confirmation ---------------------------------------


async def test_simultaneous_confirmations_produce_one_case_and_one_workflow(
    repository: OperationalRepository, temporal: Client
) -> None:
    """CASE-01, and contract C1, against real Mongo and real Temporal.

    Eight confirmations of the same order, in the same conversation, released
    together. The audit recorded "idempotency key exists; true concurrent test
    absent" -- the key is a unique partial index on `confirmationKey` and the
    execution id is derived from the case id, and neither had ever been asked
    to arbitrate a real race.

    Both halves are asserted, because either one alone would leave the contract
    broken in a way the other hides: one case with two executions means two
    workflows drive one return, and two cases with one execution means a return
    exists that nothing will ever drive.
    """
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_queue = f"test-return-case-{uuid.uuid4().hex[:12]}"
    confirmation = OrderConfirmation(
        candidate_set_id=f"cs-{uuid.uuid4().hex[:8]}",
        candidate_id=f"cand-{uuid.uuid4().hex[:8]}",
        order_reference="CW273354",
        order_line_references=("L1", "L2"),
    )
    key = confirmation.idempotency_key(tenant_id=TENANT_ID, conversation_id=conversation_id)

    # The production adapters, not a hand-rolled equivalent: the confirmation
    # path is `RepositoryCaseStore.confirm_case` followed by
    # `TemporalCaseWorkflowLauncher.ensure_case_workflow`, and a test that
    # inlined `create_case` would prove the index and not the path.
    store = RepositoryCaseStore(repository)
    launcher = TemporalCaseWorkflowLauncher(
        client=temporal,
        repository=repository,
        timings=ReturnCaseTimingConfiguration(),
        task_queue=case_queue,
    )

    async def _confirm(index: int) -> tuple[str, bool, bool]:
        del index
        confirmed = await store.confirm_case(
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            branch_ids=("CHARLOTTE",),
            conversation_id=conversation_id,
            confirmation=confirmation,
            configuration_release_id="release-under-test",
            graph_generation_id="gen-under-test",
        )
        started = await launcher.ensure_case_workflow(
            case_id=confirmed.case_id,
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            conversation_id=conversation_id,
            configuration_release_id="release-under-test",
        )
        return confirmed.case_id, confirmed.already_existed, started.already_running

    case_ids: tuple[str, ...] = ()
    try:
        results = await _released_together(CONTENDERS, _confirm)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"a confirmation raised rather than converging: {failures}"

        resolved = {str(result[0]) for result in results}
        case_ids = tuple(resolved)
        assert len(resolved) == 1, (
            f"{CONTENDERS} simultaneous confirmations resolved to {len(resolved)} cases: {resolved}"
        )
        case_id = next(iter(resolved))

        # Exactly one writer created it; the other seven adopted what they found.
        created = [result for result in results if result[1] is False]
        assert len(created) == 1, f"{len(created)} writers each believed they created the case"

        # And in the store, not merely in what the adapter reported.
        assert await repository.cases.count_documents({"confirmationKey": key}) == 1
        assert (
            await repository.cases.count_documents({"channelAConversationId": conversation_id}) == 1
        )

        # The confirmation fact is written only on the turn that creates the
        # case, so a race that created it twice would show up here even if the
        # duplicate case were later collapsed.
        assert (
            await repository.case_facts.count_documents(
                {"caseId": case_id, "factName": "confirmed_order_reference"}
            )
            == 1
        )

        # The Temporal half. One execution, and exactly one writer started it.
        workflow_id = return_case_workflow_id(case_id)
        executions = [
            execution
            async for execution in temporal.list_workflows(f"WorkflowId = '{workflow_id}'")
        ]
        assert len(executions) == 1, (
            f"{CONTENDERS} simultaneous confirmations produced {len(executions)} executions"
        )
        started_fresh = [result for result in results if result[2] is False]
        assert len(started_fresh) == 1, (
            f"{len(started_fresh)} writers each believed they started the workflow"
        )

        # The link is recorded, and points at the one execution that exists.
        stored = await repository.get_case(case_id)
        assert stored is not None
        assert stored["workflowId"] == workflow_id
    finally:
        await _terminate(temporal, case_ids)
        await _purge(repository, case_ids)


async def test_the_confirmation_index_and_not_the_read_is_what_arbitrates(
    repository: OperationalRepository,
) -> None:
    """The mechanism the test above can only sometimes reach.

    `confirm_case` reads before it writes, so when the writers happen to
    serialise, seven of them resolve the case from `find_case_by_confirmation`
    and the unique index is never asked anything. That is a legitimate
    optimisation and a bad thing to be *relying* on, because it means the
    scenario above can pass on a repository whose index was never created.

    This enters at `create_case` instead, with a different candidate case id per
    writer and no read in front of it, so every writer genuinely attempts the
    insert. Exactly one document may exist, and all eight must come away holding
    the winner's id -- which is the convergence `confirm_case` depends on and
    the only part of it a fast machine can skip.
    """
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    key = f"{TENANT_ID}|{conversation_id}|CW273354|L1"
    case_ids: tuple[str, ...] = ()
    try:

        async def _create(index: int) -> str:
            created = await repository.create_case(
                case_id=f"case-{uuid.uuid4()}",
                tenant_id=TENANT_ID,
                principal_id=PRINCIPAL_ID,
                channel_a_conversation_id=conversation_id,
                confirmed_order_reference="CW273354",
                confirmation_key=key,
            )
            del index
            return str(created["caseId"])

        results = await _released_together(CONTENDERS, _create)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, (
            f"a duplicate insert escaped as an error instead of resolving: {failures}"
        )

        resolved = {str(result) for result in results}
        assert len(resolved) == 1, (
            f"{CONTENDERS} concurrent inserts on one confirmation key produced "
            f"{len(resolved)} distinct case ids: {resolved}"
        )
        case_ids = tuple(resolved)
        assert await repository.cases.count_documents({"confirmationKey": key}) == 1
    finally:
        await _purge(repository, case_ids)
        await repository.cases.delete_many({"confirmationKey": key})


async def test_confirming_a_different_order_in_one_conversation_is_a_second_case(
    repository: OperationalRepository, temporal: Client
) -> None:
    """The other half of C1: idempotency must not become collapsing.

    Two *different* orders confirmed in one conversation at the same instant are
    two returns. A key that deduplicated on the conversation alone would answer
    this test with one case and silently discard a return the associate raised.
    """
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_queue = f"test-return-case-{uuid.uuid4().hex[:12]}"
    store = RepositoryCaseStore(repository)
    launcher = TemporalCaseWorkflowLauncher(
        client=temporal,
        repository=repository,
        timings=ReturnCaseTimingConfiguration(),
        task_queue=case_queue,
    )
    references = ("CW273354", "CW999111")

    async def _confirm(index: int) -> str:
        confirmation = OrderConfirmation(
            candidate_set_id=f"cs-{index}",
            candidate_id=f"cand-{index}",
            order_reference=references[index % len(references)],
            order_line_references=("L1",),
        )
        confirmed = await store.confirm_case(
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            branch_ids=("CHARLOTTE",),
            conversation_id=conversation_id,
            confirmation=confirmation,
            configuration_release_id="release-under-test",
            graph_generation_id="gen-under-test",
        )
        started = await launcher.ensure_case_workflow(
            case_id=confirmed.case_id,
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            conversation_id=conversation_id,
            configuration_release_id="release-under-test",
        )
        assert started.workflow_id == return_case_workflow_id(confirmed.case_id)
        return confirmed.case_id

    case_ids: tuple[str, ...] = ()
    try:
        results = await _released_together(len(references) * 2, _confirm)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"a confirmation raised: {failures}"
        resolved = {str(result) for result in results}
        case_ids = tuple(resolved)
        assert len(resolved) == 2, (
            f"two distinct orders in one conversation resolved to {len(resolved)} cases"
        )
        # And one execution each -- two cases, two workflows, no crossover.
        for case_id in resolved:
            workflow_id = return_case_workflow_id(case_id)
            executions = [
                execution
                async for execution in temporal.list_workflows(f"WorkflowId = '{workflow_id}'")
            ]
            assert len(executions) == 1
    finally:
        await _terminate(temporal, case_ids)
        await _purge(repository, case_ids)


# --- §29 matrix: concurrent case fact writers ---------------------------------


async def _seed_case(repository: OperationalRepository) -> str:
    case_id = str(uuid.uuid4())
    await repository.create_case(
        case_id=case_id,
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        channel_a_conversation_id=f"conv-{uuid.uuid4().hex[:12]}",
        confirmed_order_reference="CW273354",
        confirmation_key=f"{TENANT_ID}|{uuid.uuid4().hex}|CW273354|",
    )
    return case_id


async def test_concurrent_fact_writers_all_survive_and_the_projection_is_deterministic(
    repository: OperationalRepository,
) -> None:
    """Bay, Support, Fulfilment and Channel A write the same case at once.

    `append_case_fact` is insert-only precisely so this is safe: an
    update-in-place would make the last writer win and drop what the others
    learned. The claim is therefore stronger than "no error" -- every writer's
    observation must still be in the log afterwards, and the projection must
    pick one of them by a rule rather than by arrival order.
    """
    case_id = await _seed_case(repository)
    try:

        async def _write(index: int) -> dict[str, Any]:
            return await repository.append_case_fact(
                fact_id=f"fact-{case_id}-{index}",
                case_id=case_id,
                fact_name="return_reason",
                value=f"reason-{index}",
                agent_id=f"agent-{index}",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
            )

        results = await _released_together(CONTENDERS, _write)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"an append-only fact writer failed: {failures}"

        # Nothing was lost. This is the property the append-only log exists for.
        stored = await repository.list_case_facts(case_id)
        assert len(stored) == CONTENDERS
        assert {str(fact["value"]) for fact in stored} == {
            f"reason-{index}" for index in range(CONTENDERS)
        }

        # And exactly one is current, chosen by (recordedAt, factId) rather than
        # by whichever insert the server happened to commit last.
        latest = await repository.latest_case_facts(case_id)
        assert set(latest) == {"return_reason"}
        expected = max(stored, key=lambda fact: (fact["recordedAt"], str(fact["factId"])))
        assert latest["return_reason"]["factId"] == expected["factId"]

        # Stable: the projection is a pure function of the log, so re-reading
        # cannot produce a different current value.
        assert (await repository.latest_case_facts(case_id))["return_reason"]["factId"] == expected[
            "factId"
        ]
    finally:
        await _purge(repository, (case_id,))


async def test_concurrent_case_updates_are_arbitrated_by_the_version_check(
    repository: OperationalRepository,
) -> None:
    """The `expected_version` CAS the audit found untested.

    Eight writers read version 0 and all eight try to move the case from it.
    Exactly one may win: a lost-update here is a case status two components
    disagree about, and `ConcurrencyConflictError` is what tells the loser to
    re-read rather than to assume it wrote.
    """
    case_id = await _seed_case(repository)
    try:
        stored = await repository.get_case(case_id)
        assert stored is not None
        version = int(stored["version"])

        async def _update(index: int) -> dict[str, Any]:
            return await repository.update_case(
                case_id, {"status": f"STATUS_{index}"}, expected_version=version
            )

        results = await _released_together(CONTENDERS, _update)
        winners = [result for result in results if not isinstance(result, BaseException)]
        losers = [result for result in results if isinstance(result, BaseException)]

        assert len(winners) == 1, f"{len(winners)} writers each won the same compare-and-set"
        assert len(losers) == CONTENDERS - 1
        assert all(isinstance(error, ConcurrencyConflictError) for error in losers), (
            f"a loser failed with something other than a concurrency conflict: {losers}"
        )

        # One increment, and the winner's value is the one stored.
        final = await repository.get_case(case_id)
        assert final is not None
        assert int(final["version"]) == version + 1
        assert final["status"] == winners[0]["status"]
    finally:
        await _purge(repository, (case_id,))


# --- §29 matrix: duplicate Support submission and two RMAs on one case ---------


async def test_a_duplicate_support_submission_issues_one_rma(
    repository: OperationalRepository,
) -> None:
    """Support pressing send twice, or a transport redelivering, at the same instant.

    The workflow ignores a second `support_response` for a work item it has
    already resolved, and that is proven at the workflow boundary elsewhere.
    This is the layer beneath it: if two `record_support_outcome` runs for the
    same RMA reached the store together -- an activity retry overlapping its own
    slow first attempt -- the unique partial index on
    `(caseId, returnReference)` must be what stops the second, because nothing
    above it can.
    """
    case_id = await _seed_case(repository)
    try:

        async def _issue(index: int) -> dict[str, Any]:
            return await repository.create_return_record(
                # A distinct record id per attempt, deliberately: keying the
                # test on one id would prove `returnRecordId`'s uniqueness and
                # not the rule that matters, which is that one RMA cannot be
                # recorded twice against one case however it is identified.
                return_record_id=f"rr-{case_id}-{index}",
                case_id=case_id,
                return_reference="RMA-DUPLICATE-SUBMISSION",
                status="ISSUED",
                source_system="RETURN_SUPPORT",
            )

        results = await _released_together(CONTENDERS, _issue)
        winners = [result for result in results if not isinstance(result, BaseException)]
        assert len(winners) == 1, f"{len(winners)} writers each issued the same RMA"

        records = await repository.list_return_records(case_id)
        assert len(records) == 1
        assert records[0]["returnReference"] == "RMA-DUPLICATE-SUBMISSION"
    finally:
        await _purge(repository, (case_id,))


async def test_two_rmas_on_one_case_stay_two_records_each_owning_its_own_label(
    repository: OperationalRepository,
) -> None:
    """Contract C3, under contention.

    Support answering one case with two RMAs is the shape the case model was
    changed to carry. The index that stops a duplicate must not also stop a
    legitimate second record, and each record must keep its *own* label and
    tracking -- a flattening would leave both RMAs sharing whichever label was
    written last.
    """
    case_id = await _seed_case(repository)
    references = tuple(f"RMA-{index}" for index in range(4))
    try:

        async def _issue(index: int) -> dict[str, Any]:
            record = await repository.create_return_record(
                return_record_id=f"rr-{case_id}-{index}",
                case_id=case_id,
                return_reference=references[index],
                status="ISSUED",
                source_system="RETURN_SUPPORT",
            )
            await repository.update_return_record(
                record["returnRecordId"],
                {
                    "labelReference": f"LBL-{index}",
                    "trackingReference": f"TRK-{index}",
                    "returnLocation": f"WH-1/BAY-{index}",
                },
                expected_version=0,
            )
            return record

        results = await _released_together(len(references), _issue)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"a distinct RMA on the same case was rejected: {failures}"

        records = await repository.list_return_records(case_id)
        assert len(records) == len(references)
        # Each record owns its own label, tracking and location -- no flattening.
        assert {str(record["returnReference"]) for record in records} == set(references)
        by_reference = {str(record["returnReference"]): record for record in records}
        for index, reference in enumerate(references):
            assert by_reference[reference]["labelReference"] == f"LBL-{index}"
            assert by_reference[reference]["trackingReference"] == f"TRK-{index}"
            assert by_reference[reference]["returnLocation"] == f"WH-1/BAY-{index}"
    finally:
        await _purge(repository, (case_id,))


async def test_an_rma_reference_is_unique_only_within_its_own_case(
    repository: OperationalRepository,
) -> None:
    """The duplicate rule is case-scoped, and must stay so under contention.

    A tenant-wide unique constraint would make two unrelated cases collide on a
    reference a source system reused, and the loser would silently lose its RMA.
    """
    first = await _seed_case(repository)
    second = await _seed_case(repository)
    try:
        cases = (first, second)

        async def _issue(index: int) -> dict[str, Any]:
            return await repository.create_return_record(
                return_record_id=f"rr-{uuid.uuid4().hex[:12]}",
                case_id=cases[index],
                return_reference="RMA-SHARED-REFERENCE",
                status="ISSUED",
                source_system="RETURN_SUPPORT",
            )

        results = await _released_together(len(cases), _issue)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"the same reference on two different cases collided: {failures}"
        assert len(await repository.list_return_records(first)) == 1
        assert len(await repository.list_return_records(second)) == 1
    finally:
        await _purge(repository, (first, second))


# --- §29 matrix: concurrent bay recommendation --------------------------------


class _StubPlacement:
    """A placement that always answers, and counts how often it was asked."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_first = False

    async def recommend(self, case_id: str) -> Any:
        del case_id
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("placement was briefly unavailable")
        # A slice of real work, so two concurrent callers are genuinely
        # overlapping rather than completing inside one scheduler slot.
        await asyncio.sleep(0.05)
        return _Recommendation()


class _Recommendation:
    warehouse_reference = "WH-1"
    bay_reference = "BAY-7"
    reason = "RECOMMENDED"
    return_location = "WH-1/BAY-7"
    confidence_millionths = 750_000
    explanation = "The recommendation is advisory."
    evidence_reference = "WAREHOUSE_OBSERVED:gen-1:3"
    graph_generation_id = "gen-1"
    capacity_evidence = None


def _bay_activities(repository: OperationalRepository, placement: _StubPlacement) -> Any:
    from return_platform.workflows.return_case_activities import ReturnCaseActivities

    return ReturnCaseActivities(
        repository=repository, support_service=None, bay_placement=placement
    )


async def test_a_concurrent_bay_recommendation_leaves_one_value_per_fact(
    repository: OperationalRepository,
) -> None:
    """Contract C2: one coherent bay result, not two opinions.

    Two recommendations for one case can overlap -- an early `bay_result` signal
    beside the activity, or the activity racing its own retry. Whatever happens,
    the case must not end up carrying two values for `bay_reference`, because
    the fact projection would then answer differently depending on which write
    landed last and the associate would be told a bay that depends on timing.
    """
    from return_platform.workflows.return_case_workflow import RequestBayAssignmentInput

    case_id = await _seed_case(repository)
    try:
        activities = _bay_activities(repository, _StubPlacement())
        request = RequestBayAssignmentInput(case_id=case_id, tenant_id=TENANT_ID)

        async def _recommend(index: int) -> Any:
            del index
            return await activities.request_bay_assignment(request)

        results = await _released_together(2, _recommend)
        answered = [result for result in results if not isinstance(result, BaseException)]
        assert answered, f"no caller obtained a recommendation: {results}"

        facts = await repository.list_case_facts(case_id)
        bay_facts = [fact for fact in facts if str(fact["factName"]).startswith("bay_")]
        names = [str(fact["factName"]) for fact in bay_facts]
        assert len(names) == len(set(names)), (
            f"a concurrent recommendation left two values for one bay fact: {sorted(names)}"
        )
        projected = await repository.latest_case_facts(case_id)
        assert projected["bay_reference"]["value"] == "BAY-7"
        assert projected["bay_return_location"]["value"] == "WH-1/BAY-7"
    finally:
        await _purge(repository, (case_id,))


async def test_the_bay_activity_is_idempotent_under_the_retry_its_policy_grants(
    repository: OperationalRepository,
) -> None:
    """The retry `_BEST_EFFORT_RETRY` exists to allow.

    Attempt one appends the request fact and then placement fails transiently.
    Temporal retries with identical input, and that attempt must be able to
    reach placement -- otherwise `maximum_attempts=2` buys nothing and a
    momentary warehouse blip costs the case its bay permanently.

    This was BAY-CONC-01, and it xfailed here until the append was fixed.
    `request_bay_assignment` wrote `bay_assignment_requested` under the derived
    id `bay-requested-<case_id>` with `append_case_fact`, which is `insert_one`
    against a unique `factId` index -- so the second attempt raised
    `DuplicateKeyError` *before* reaching placement and the one retry the policy
    grants could never succeed. The log is still insert-only; the append now
    goes through `_append_fact_once`, which recognises its own previous attempt.
    """
    from return_platform.workflows.return_case_workflow import RequestBayAssignmentInput

    case_id = await _seed_case(repository)
    try:
        placement = _StubPlacement()
        placement.fail_first = True
        activities = _bay_activities(repository, placement)
        request = RequestBayAssignmentInput(case_id=case_id, tenant_id=TENANT_ID)

        with pytest.raises(RuntimeError):
            await activities.request_bay_assignment(request)

        # The retry Temporal makes, with identical input.
        notice = await activities.request_bay_assignment(request)
        assert notice.bay_reference == "BAY-7"
        assert placement.calls == 2, "the retry never reached placement"

        # The marker fact is recorded once, not twice: absorbing the duplicate
        # must not have turned the insert-only log into an append-twice one.
        facts = await repository.list_case_facts(case_id)
        markers = [fact for fact in facts if str(fact["factName"]) == "bay_assignment_requested"]
        assert len(markers) == 1, f"the retry appended a second marker fact: {markers}"

        # And the recommendation the retry finally obtained is the one on the
        # case -- a retry that reached placement but recorded nothing would
        # leave the associate reading no bay at all.
        projected = await repository.latest_case_facts(case_id)
        assert projected["bay_reference"]["value"] == "BAY-7"
        assert projected["bay_return_location"]["value"] == "WH-1/BAY-7"
    finally:
        await _purge(repository, (case_id,))


# --- §29 matrix: the workflow link under contention ---------------------------


async def test_concurrent_workflow_links_bind_once_and_never_repoint(
    repository: OperationalRepository,
) -> None:
    """`bind_case_workflow` is the link a recovery sweep also writes.

    Two writers can reach it at once for one case -- the confirmation that just
    started the execution, and `return_case_recovery` sweeping a case whose link
    was not recorded. Both must converge on one id, and a *different* id must
    raise rather than silently repoint the case at another execution, because
    `return_support.py` derives the id from the case and would then signal an
    execution the case no longer claims.
    """
    case_id = await _seed_case(repository)
    try:
        workflow_id = return_case_workflow_id(case_id)

        async def _bind(index: int) -> bool:
            del index
            return await repository.bind_case_workflow(case_id, workflow_id=workflow_id)

        results = await _released_together(CONTENDERS, _bind)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"binding the same workflow id twice raised: {failures}"
        assert sum(1 for result in results if result is True) == 1, (
            "more than one writer believed it recorded the link"
        )

        stored = await repository.get_case(case_id)
        assert stored is not None
        assert stored["workflowId"] == workflow_id

        # A different execution for a case that already has one is a broken
        # invariant, not a re-link.
        with pytest.raises(ConcurrencyConflictError):
            await repository.bind_case_workflow(case_id, workflow_id="return-case-somewhere-else")
    finally:
        await _purge(repository, (case_id,))
