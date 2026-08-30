"""S2: per-case delivery streams on the integration outbox (contracts.md sect. 7).

Every event carries `event_id` / `causation_id` / `required_predecessor_ids[]`,
validated at enqueue; sequences are CAS-allocated per `(case, stream)`;
dispatch waits for predecessors; a dead-lettered predecessor parks its stream
and only an audited operator skip or retry resumes it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.fact_names import SUPPORT_STREAM_SKIP
from return_platform.operations.integrations.outbox import (
    DEAD_LETTER_STATUS,
    PARKED_STREAM_STATUS,
    PREDECESSOR_DEAD_LETTERED,
    REQUIRES_RECONCILIATION,
    WAITING_ON_PREDECESSOR,
    CaseStream,
    DispatchResult,
    IntegrationOutboxDispatcher,
    OutboxCommand,
    PermanentDeliveryFailure,
    PredecessorCycleError,
    TopicDispatcher,
    UnknownPredecessorError,
    allocate_case_stream_sequence,
    ensure_integration_outbox_indexes,
    ordered_command_fields,
)
from return_platform.workflows.return_case_recovery import CaseStreamRecovery
from tests.operations.mongo_double import FakeClient, FakeCollection

CASE_ID = "case-7001"
OTHER_CASE_ID = "case-7002"
TOPIC = "return-case.support-outbound-test"


class _RecordingDispatcher:
    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.dispatched: list[str] = []
        self._failures = failures or []

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        if self._failures:
            raise self._failures.pop(0)
        self.dispatched.append(command.event_id or command.id)
        return DispatchResult(external_reference=None, response_digest=None)


class _RecordingFactRepository:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []

    async def append_scoped_case_fact(self, **fact: Any) -> dict[str, Any]:
        if any(existing["fact_id"] == fact["fact_id"] for existing in self.facts):
            raise DuplicateKeyError("duplicate factId")
        self.facts.append(fact)
        return fact


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def database(mongo: FakeClient, test_settings: Settings) -> Any:
    return mongo[test_settings.mongo_database]


@pytest.fixture
def outbox(database: Any) -> FakeCollection:
    return database["integration_outbox"]


async def _enqueue(
    database: Any,
    *,
    case_id: str = CASE_ID,
    stream: CaseStream = CaseStream.OUTBOUND,
    event_id: str,
    predecessors: tuple[str, ...] = (),
    status: str = "PENDING",
) -> dict[str, Any]:
    """Build one ordered command the way a production enqueuer would."""
    ordering = await ordered_command_fields(
        database,
        case_id=case_id,
        stream=stream,
        event_id=event_id,
        required_predecessor_ids=predecessors,
    )
    now = datetime.now(UTC)
    document = {
        "_id": f"cmd-{event_id}",
        "topic": TOPIC,
        "aggregateType": "RETURN_CASE",
        "aggregateId": case_id,
        "idempotencyKey": f"key-{event_id}",
        "payload": {"caseId": case_id},
        "status": status,
        "attemptCount": 0,
        "nextAttemptAt": now - timedelta(seconds=1),
        "createdAt": now,
        "updatedAt": now,
        **ordering,
    }
    await database["integration_outbox"].insert_one(document)
    return document


def _worker(mongo: FakeClient, settings: Settings, dispatcher: _RecordingDispatcher) -> Any:
    return IntegrationOutboxDispatcher(
        cast(Any, mongo),
        settings,
        {TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )


async def _drain(worker: Any, rounds: int, outbox: FakeCollection) -> None:
    for _ in range(rounds):
        for document in outbox.documents.values():
            if document["status"] in ("PENDING", "RETRY"):
                document["nextAttemptAt"] = datetime.now(UTC) - timedelta(seconds=1)
        if not await worker.dispatch_once():
            return


# --------------------------------------------------------------------------- #
# Sequence allocation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sequences_are_allocated_by_cas_and_never_collide(database: Any) -> None:
    sequences = await asyncio.gather(
        *(
            allocate_case_stream_sequence(
                database, case_id=CASE_ID, stream=CaseStream.REVIEW_COMMANDS
            )
            for _ in range(10)
        )
    )
    assert sorted(sequences) == list(range(1, 11))


@pytest.mark.asyncio
async def test_each_case_and_stream_counts_for_itself(database: Any) -> None:
    a = await allocate_case_stream_sequence(database, case_id=CASE_ID, stream=CaseStream.INBOUND)
    b = await allocate_case_stream_sequence(database, case_id=CASE_ID, stream=CaseStream.OMC)
    c = await allocate_case_stream_sequence(
        database, case_id=OTHER_CASE_ID, stream=CaseStream.INBOUND
    )
    assert (a, b, c) == (1, 1, 1)


@pytest.mark.asyncio
async def test_the_unique_stream_index_is_created(database: Any, outbox: FakeCollection) -> None:
    await ensure_integration_outbox_indexes(database)
    names = {options.get("name") for _keys, options in outbox.index_calls}
    assert "case_stream_sequence_unique" in names
    assert "case_stream_event_id_unique" in names


# --------------------------------------------------------------------------- #
# Enqueue validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_unknown_predecessor_is_refused_at_enqueue(database: Any) -> None:
    with pytest.raises(UnknownPredecessorError):
        await ordered_command_fields(
            database,
            case_id=CASE_ID,
            stream=CaseStream.OUTBOUND,
            event_id="evt-b",
            required_predecessor_ids=("evt-never-enqueued",),
        )


@pytest.mark.asyncio
async def test_an_event_cannot_precede_itself(database: Any) -> None:
    with pytest.raises(PredecessorCycleError):
        await ordered_command_fields(
            database,
            case_id=CASE_ID,
            stream=CaseStream.OUTBOUND,
            event_id="evt-a",
            required_predecessor_ids=("evt-a",),
        )


@pytest.mark.asyncio
async def test_a_valid_chain_gets_sequence_and_fields(database: Any) -> None:
    first = await _enqueue(database, event_id="evt-1")
    fields = await ordered_command_fields(
        database,
        case_id=CASE_ID,
        stream=CaseStream.OUTBOUND,
        event_id="evt-2",
        causation_id="evt-1",
        required_predecessor_ids=("evt-1",),
    )
    assert first["streamSequence"] == 1
    assert fields == {
        "stream": "outbound",
        "streamSequence": 2,
        "eventId": "evt-2",
        "causationId": "evt-1",
        "requiredPredecessorIds": ["evt-1"],
    }


# --------------------------------------------------------------------------- #
# Dispatch ordering
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_parentless_event_dispatches_immediately(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    await _enqueue(database, event_id="evt-lone")
    dispatcher = _RecordingDispatcher()
    assert await _worker(mongo, test_settings, dispatcher).dispatch_once() is True
    assert dispatcher.dispatched == ["evt-lone"]
    assert outbox.documents["cmd-evt-lone"]["status"] == "DELIVERED"


@pytest.mark.asyncio
async def test_a_dependent_waits_for_its_predecessor_without_burning_an_attempt(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    await _enqueue(database, event_id="evt-first")
    await _enqueue(database, event_id="evt-second", predecessors=("evt-first",))
    # The predecessor is not claimable yet -- only the dependent is.
    outbox.documents["cmd-evt-first"]["nextAttemptAt"] = datetime.now(UTC) + timedelta(hours=1)

    dispatcher = _RecordingDispatcher()
    worker = _worker(mongo, test_settings, dispatcher)
    assert await worker.dispatch_once() is True

    deferred = outbox.documents["cmd-evt-second"]
    assert dispatcher.dispatched == []
    assert deferred["status"] == "RETRY"
    assert deferred["lastErrorCode"] == WAITING_ON_PREDECESSOR
    assert deferred["attemptCount"] == 0, "an ordering wait is not a delivery attempt"

    # Predecessor delivers; the dependent follows.
    outbox.documents["cmd-evt-first"]["nextAttemptAt"] = datetime.now(UTC) - timedelta(seconds=1)
    await _drain(worker, 4, outbox)
    assert dispatcher.dispatched == ["evt-first", "evt-second"]


@pytest.mark.asyncio
async def test_a_dead_lettered_predecessor_parks_its_stream_and_only_its_stream(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    dead = await _enqueue(database, event_id="evt-dead")
    outbox.documents[str(dead["_id"])]["status"] = DEAD_LETTER_STATUS
    outbox.documents[str(dead["_id"])]["reconciliationState"] = REQUIRES_RECONCILIATION
    await _enqueue(database, event_id="evt-dependent", predecessors=("evt-dead",))
    await _enqueue(database, event_id="evt-same-stream")
    await _enqueue(database, event_id="evt-other-stream", stream=CaseStream.INBOUND)
    await _enqueue(database, event_id="evt-other-case", case_id=OTHER_CASE_ID)

    dispatcher = _RecordingDispatcher()
    worker = _worker(mongo, test_settings, dispatcher)
    await _drain(worker, 8, outbox)

    assert outbox.documents["cmd-evt-dependent"]["status"] == PARKED_STREAM_STATUS
    assert outbox.documents["cmd-evt-dependent"]["parkedReason"] == PREDECESSOR_DEAD_LETTERED
    assert outbox.documents["cmd-evt-same-stream"]["status"] == PARKED_STREAM_STATUS
    # The other stream and the other case are untouched.
    assert outbox.documents["cmd-evt-other-stream"]["status"] == "DELIVERED"
    assert outbox.documents["cmd-evt-other-case"]["status"] == "DELIVERED"
    assert "evt-dependent" not in dispatcher.dispatched
    assert "evt-same-stream" not in dispatcher.dispatched


# --------------------------------------------------------------------------- #
# Operator skip / retry
# --------------------------------------------------------------------------- #


async def _parked_fixture(
    mongo: FakeClient, database: Any, settings: Settings, outbox: FakeCollection
) -> tuple[Any, _RecordingDispatcher]:
    dead = await _enqueue(database, event_id="evt-dead")
    outbox.documents[str(dead["_id"])]["status"] = DEAD_LETTER_STATUS
    await _enqueue(database, event_id="evt-dependent", predecessors=("evt-dead",))
    dispatcher = _RecordingDispatcher()
    worker = _worker(mongo, settings, dispatcher)
    await _drain(worker, 4, outbox)
    assert outbox.documents["cmd-evt-dependent"]["status"] == PARKED_STREAM_STATUS
    return worker, dispatcher


@pytest.mark.asyncio
async def test_an_audited_skip_writes_the_fact_and_resumes_the_stream(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    worker, dispatcher = await _parked_fixture(mongo, database, test_settings, outbox)
    facts = _RecordingFactRepository()
    recovery = CaseStreamRecovery(database, fact_repository=facts)

    assert (
        await recovery.skip_dead_lettered_command(
            case_id=CASE_ID,
            stream=CaseStream.OUTBOUND,
            event_id="evt-dead",
            actor_id="operator-1",
            reason="support confirmed the message is obsolete",
        )
        is True
    )

    dead = outbox.documents["cmd-evt-dead"]
    assert dead["status"] == DEAD_LETTER_STATUS, "skip resolves ordering, not the dead letter"
    assert dead["orderingResolved"] is True
    assert dead["orderingResolution"]["action"] == "SKIPPED"
    assert dead["orderingResolution"]["actorId"] == "operator-1"

    assert len(facts.facts) == 1
    fact = facts.facts[0]
    assert fact["fact_name"] == SUPPORT_STREAM_SKIP
    assert fact["case_id"] == CASE_ID
    assert fact["value"]["event_id"] == "evt-dead"
    assert fact["record_scope"] is None

    await _drain(worker, 4, outbox)
    assert "evt-dependent" in dispatcher.dispatched

    # A second skip of the same event matches nothing and records nothing.
    assert (
        await recovery.skip_dead_lettered_command(
            case_id=CASE_ID,
            stream=CaseStream.OUTBOUND,
            event_id="evt-dead",
            actor_id="operator-2",
            reason="again",
        )
        is False
    )
    assert len(facts.facts) == 1


@pytest.mark.asyncio
async def test_an_operator_retry_requeues_the_dead_letter_and_resumes(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    worker, dispatcher = await _parked_fixture(mongo, database, test_settings, outbox)
    recovery = CaseStreamRecovery(database, fact_repository=_RecordingFactRepository())

    assert (
        await recovery.retry_dead_lettered_command(
            case_id=CASE_ID, stream=CaseStream.OUTBOUND, event_id="evt-dead"
        )
        is True
    )
    assert outbox.documents["cmd-evt-dead"]["status"] == "PENDING"
    assert outbox.documents["cmd-evt-dependent"]["status"] == "PENDING"

    await _drain(worker, 6, outbox)
    assert dispatcher.dispatched == ["evt-dead", "evt-dependent"]


@pytest.mark.asyncio
async def test_a_late_enqueue_into_a_parked_stream_is_parked_too(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    """The bulk flip is visibility; the earlier-dead-letter query is the guard."""
    worker, dispatcher = await _parked_fixture(mongo, database, test_settings, outbox)
    await _enqueue(database, event_id="evt-late")

    await _drain(worker, 4, outbox)
    assert "evt-late" not in dispatcher.dispatched
    assert outbox.documents["cmd-evt-late"]["status"] == PARKED_STREAM_STATUS


@pytest.mark.asyncio
async def test_a_dead_letter_downstream_never_parks_what_came_before(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    """Parking is about *predecessors*: a failure later in the stream must not
    stop earlier, independent commands from delivering."""
    await _enqueue(database, event_id="evt-early")
    late = await _enqueue(database, event_id="evt-late-dead")
    outbox.documents[str(late["_id"])]["status"] = DEAD_LETTER_STATUS

    dispatcher = _RecordingDispatcher()
    await _drain(_worker(mongo, test_settings, dispatcher), 4, outbox)
    assert dispatcher.dispatched == ["evt-early"]


@pytest.mark.asyncio
async def test_a_permanent_failure_still_dead_letters_an_ordered_command(
    mongo: FakeClient, database: Any, test_settings: Settings, outbox: FakeCollection
) -> None:
    await _enqueue(database, event_id="evt-doomed")
    dispatcher = _RecordingDispatcher(
        failures=[PermanentDeliveryFailure("NOPE", error_code="NOPE")]
    )
    await _drain(_worker(mongo, test_settings, dispatcher), 2, outbox)
    doomed = outbox.documents["cmd-evt-doomed"]
    assert doomed["status"] == DEAD_LETTER_STATUS
    assert doomed["reconciliationState"] == REQUIRES_RECONCILIATION
