"""V2: what an inbound support message commits to (contracts.md sect. 5, sect. 7).

Four claims, and each is tested as a property that would break rather than as a
call that would succeed:

* **dedupe** -- the same identity twice is one event and one classify command;
  the same identity carrying different words is a refusal, not a silent
  overwrite;
* **parking** -- a shut door never 409s, never enqueues, and never loses the
  message; the backlog re-enters the chain *before* anything newer;
* **the causation chain** -- acceptance 18's ordered drain is only real if the
  enqueuing store fills `causation_id` and `required_predecessor_ids`, so the
  chain is asserted directly *and* through S2's real dispatcher, which is the
  thing the guarantee is actually about;
* **the raw body survives** -- the words are evidence, the analysis is an
  interpretation, and one may never overwrite the other.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.settings import Settings
from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
    SupportParkingConfiguration,
)
from return_platform.operations.integrations.outbox import (
    WAITING_ON_PREDECESSOR,
    CaseStream,
    DispatchResult,
    IntegrationOutboxDispatcher,
    OutboxCommand,
    TopicDispatcher,
    ensure_integration_outbox_indexes,
)
from return_platform.operations.return_support.ingress import (
    NormalizedSupportEvent,
    SupportEventStatus,
    SupportInboundMessage,
    normalize_inbound_message,
)
from return_platform.operations.return_support.ingress_store import (
    SUPPORT_MESSAGE_CLASSIFY_TOPIC,
    DurableSupportIngressStore,
    inbound_chain,
)
from return_platform.operations.support_events import IdempotencyConflictError
from tests.operations.mongo_double import FakeClient, FakeCollection, FakeSession

CASE_ID = "case-9001"
WORK_ITEM = "wi-9001"
WORKFLOW_ID = "return-case-9001"
ACTOR = "support-ingress"


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def database(mongo: FakeClient, test_settings: Settings) -> Any:
    return mongo[test_settings.mongo_database]


@pytest.fixture
def outbox(database: Any) -> FakeCollection:
    return database["integration_outbox"]


def _configuration(**parking: int) -> SupportIngressConfiguration:
    return SupportIngressConfiguration(
        parking=SupportParkingConfiguration(**parking) if parking else SupportParkingConfiguration()
    )


@pytest_asyncio.fixture
async def store(
    mongo: FakeClient, test_settings: Settings, database: Any
) -> DurableSupportIngressStore:
    built = DurableSupportIngressStore(cast(Any, mongo), test_settings, _configuration())
    await built.ensure_indexes()
    await ensure_integration_outbox_indexes(database)
    return built


def _event(
    *,
    external_message_id: str = "m-1",
    body: str = "RMA-1 is issued, tracking 1Z-AAA.",
    channel: str = "email",
) -> NormalizedSupportEvent:
    return normalize_inbound_message(
        SupportInboundMessage(
            external_message_id=external_message_id,
            body_text=body,
            sender="support-agent-7",
            channel_hint=channel,
        ),
        case_id=CASE_ID,
        work_item_id=WORK_ITEM,
    )


async def _record(
    store: DurableSupportIngressStore,
    event: NormalizedSupportEvent,
    *,
    nl_enabled: bool = True,
) -> Any:
    return await store.record_inbound_message(
        event=event,
        workflow_id=WORKFLOW_ID,
        actor_id=ACTOR,
        nl_enabled=nl_enabled,
    )


# --------------------------------------------------------------------------- #
# Dedupe
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_same_message_twice_is_one_event_and_one_classify_command(
    store: DurableSupportIngressStore, outbox: FakeCollection
) -> None:
    first = await _record(store, _event())
    second = await _record(store, _event())

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.support_event_id == first.support_event_id
    assert second.outbox_command_id == first.outbox_command_id

    classify = [
        document
        for document in outbox.documents.values()
        if document["topic"] == SUPPORT_MESSAGE_CLASSIFY_TOPIC
    ]
    assert len(classify) == 1, "a redelivery must not queue a second analysis"
    assert len(await store.list_inbound(CASE_ID)) == 1


@pytest.mark.asyncio
async def test_the_same_identity_carrying_different_words_is_refused(
    store: DurableSupportIngressStore,
) -> None:
    """Not a silent overwrite. The stored words are what the case believes."""
    await _record(store, _event(body="Tracking is 1Z-AAA."))
    with pytest.raises(IdempotencyConflictError):
        await _record(store, _event(body="Actually, the return is rejected."))

    stored = await store.get_inbound(support_event_id=_event().support_event_id)
    assert stored is not None
    assert stored["rawBody"] == "Tracking is 1Z-AAA."


@pytest.mark.asyncio
async def test_two_transports_carrying_the_same_external_id_are_two_events(
    store: DurableSupportIngressStore,
) -> None:
    email = await _record(store, _event(external_message_id="shared", channel="email"))
    chat = await _record(store, _event(external_message_id="shared", channel="teams"))
    assert email.support_event_id != chat.support_event_id
    assert len(await store.list_inbound(CASE_ID)) == 2


@pytest.mark.asyncio
async def test_the_raw_body_is_kept_beside_the_normalized_event(
    store: DurableSupportIngressStore,
) -> None:
    await _record(store, _event(body="Label LBL-9 is attached."))
    stored = await store.get_inbound(support_event_id=_event().support_event_id)
    assert stored is not None
    assert stored["rawBody"] == "Label LBL-9 is attached."
    assert stored["normalizedEvent"]["caseId"] == CASE_ID
    # Unanalysed on arrival: the intent is a model's answer and has not been
    # asked for yet.
    assert stored["normalizedEvent"]["intent"] is None


# --------------------------------------------------------------------------- #
# Parking
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_shut_door_parks_and_never_refuses(
    store: DurableSupportIngressStore, outbox: FakeCollection
) -> None:
    receipt = await _record(store, _event(), nl_enabled=False)

    assert receipt.status == SupportEventStatus.PARKED
    assert receipt.outbox_command_id is None
    assert receipt.parked_count == 1
    assert not [
        document
        for document in outbox.documents.values()
        if document["topic"] == SUPPORT_MESSAGE_CLASSIFY_TOPIC
    ], "a parked message must not be queued for analysis"
    stored = await store.get_inbound(support_event_id=_event().support_event_id)
    assert stored is not None and stored["rawBody"]


@pytest.mark.asyncio
async def test_a_redelivered_message_never_409s_just_because_it_parked(
    store: DurableSupportIngressStore,
) -> None:
    """Contracts.md sect. 5: parked messages are never 409'd."""
    first = await _record(store, _event(), nl_enabled=False)
    second = await _record(store, _event(), nl_enabled=False)
    assert second.duplicate is True
    assert second.status == SupportEventStatus.PARKED
    assert second.support_event_id == first.support_event_id


@pytest.mark.asyncio
async def test_flipping_the_switch_reprocesses_the_backlog_before_anything_new(
    store: DurableSupportIngressStore, outbox: FakeCollection
) -> None:
    """The ordering claim, at the level that matters.

    Two messages park. The switch flips and a third arrives. The third must not
    reach the dispatcher ahead of the two that were waiting -- and "must not"
    here means *the chain forbids it*, not that the enqueue happened to be in
    that order.
    """
    base = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    for offset, identifier in enumerate(("m-1", "m-2")):
        await store.record_inbound_message(
            event=_event(external_message_id=identifier),
            workflow_id=WORKFLOW_ID,
            actor_id=ACTOR,
            nl_enabled=False,
            now=base + timedelta(seconds=offset),
        )
    assert await store.parked_count(CASE_ID) == 2

    await store.record_inbound_message(
        event=_event(external_message_id="m-3"),
        workflow_id=WORKFLOW_ID,
        actor_id=ACTOR,
        nl_enabled=True,
        now=base + timedelta(seconds=10),
    )

    assert await store.parked_count(CASE_ID) == 0
    chain = inbound_chain(list(outbox.documents.values()))
    ids = [event_id for event_id, _ in chain]
    assert ids == [
        _event(external_message_id="m-1").support_event_id,
        _event(external_message_id="m-2").support_event_id,
        _event(external_message_id="m-3").support_event_id,
    ]
    # And each link names the one before it, which is what stops a second
    # worker from running m-3 first.
    assert [predecessors for _, predecessors in chain] == [
        (),
        (ids[0],),
        (ids[1],),
    ]


@pytest.mark.asyncio
async def test_draining_twice_releases_nothing_the_second_time(
    store: DurableSupportIngressStore, outbox: FakeCollection
) -> None:
    """Check-then-act, safely re-runnable (contracts §3)."""
    await _record(store, _event(), nl_enabled=False)
    first = await store.drain_parked(case_id=CASE_ID, workflow_id=WORKFLOW_ID, actor_id=ACTOR)
    second = await store.drain_parked(case_id=CASE_ID, workflow_id=WORKFLOW_ID, actor_id=ACTOR)
    assert len(first) == 1
    assert second == []
    assert (
        len(
            [
                document
                for document in outbox.documents.values()
                if document["topic"] == SUPPORT_MESSAGE_CLASSIFY_TOPIC
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_parking_past_the_quota_escalates(
    mongo: FakeClient, test_settings: Settings, database: Any
) -> None:
    store = DurableSupportIngressStore(
        cast(Any, mongo), test_settings, _configuration(per_case_quota=2)
    )
    await store.ensure_indexes()
    await ensure_integration_outbox_indexes(database)

    receipts = []
    for index in range(3):
        receipts.append(
            await store.record_inbound_message(
                event=_event(external_message_id=f"m-{index}"),
                workflow_id=WORKFLOW_ID,
                actor_id=ACTOR,
                nl_enabled=False,
            )
        )
    assert [receipt.quota_exceeded for receipt in receipts] == [False, False, True]
    assert receipts[-1].parked_count == 3


@pytest.mark.asyncio
async def test_the_parking_alert_is_deduped_per_window(
    store: DurableSupportIngressStore,
) -> None:
    """One alert per case per window, not one per message.

    Asserted on the predicate rather than on log capture: the property is the
    window arithmetic, and a test that counted log lines would pass on a
    predicate that always returned True at the boundary.
    """
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    assert store.alert_should_fire(last_alerted_at=None, now=now) is True
    assert store.alert_should_fire(last_alerted_at=now - timedelta(seconds=899), now=now) is False
    assert store.alert_should_fire(last_alerted_at=now - timedelta(seconds=900), now=now) is True


# --------------------------------------------------------------------------- #
# The causation chain, through S2's real dispatcher
# --------------------------------------------------------------------------- #


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        self.dispatched.append(command.event_id or command.id)
        return DispatchResult(external_reference=None, response_digest=None)


@pytest.mark.asyncio
async def test_every_enqueued_event_carries_its_causation(
    store: DurableSupportIngressStore, outbox: FakeCollection
) -> None:
    """Sect. 7 names three fields; a chain with two of them is not a chain."""
    for identifier in ("m-1", "m-2", "m-3"):
        await _record(store, _event(external_message_id=identifier))

    ordered = sorted(
        (
            document
            for document in outbox.documents.values()
            if document.get("stream") == CaseStream.INBOUND.value
        ),
        key=lambda document: int(document["streamSequence"]),
    )
    assert [document["causationId"] for document in ordered] == [
        None,
        ordered[0]["eventId"],
        ordered[1]["eventId"],
    ]
    assert [document["requiredPredecessorIds"] for document in ordered] == [
        [],
        [ordered[0]["eventId"]],
        [ordered[1]["eventId"]],
    ]


@pytest.mark.asyncio
async def test_the_dispatcher_drains_the_inbound_stream_in_order(
    mongo: FakeClient,
    test_settings: Settings,
    store: DurableSupportIngressStore,
    outbox: FakeCollection,
) -> None:
    """Acceptance 18, proved against the machinery rather than the enqueue.

    The queue is deliberately loaded **against** the answer: the newest command
    is made the *oldest*-due, so `claim`'s `(nextAttemptAt, createdAt)` sort
    hands the worker message three first, then two, then one. Nothing about the
    enqueue order can help here -- if the causation chain this store writes were
    absent (increasing sequence numbers, empty predecessor lists), the worker
    would deliver three, two, one, which is exactly backwards.

    What actually happens is the two later messages are *held*: claimed,
    refused by `_ordering_hold`, and released with `WAITING_ON_PREDECESSOR`
    while only message one is delivered. That hold is the guarantee, and it
    exists only because `required_predecessor_ids` was populated.
    """
    ids = []
    for identifier in ("m-1", "m-2", "m-3"):
        receipt = await _record(store, _event(external_message_id=identifier))
        ids.append(receipt.support_event_id)

    by_event = {str(document["eventId"]): document for document in outbox.documents.values()}
    base = datetime.now(UTC)
    for offset, event_id in enumerate(reversed(ids)):
        # m-3 due longest ago, m-1 most recently: the worst case for ordering.
        by_event[event_id]["nextAttemptAt"] = base - timedelta(seconds=30 - offset * 10)

    dispatcher = _RecordingDispatcher()
    worker = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_MESSAGE_CLASSIFY_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )

    for _ in range(6):
        if not await worker.dispatch_once():
            break

    assert dispatcher.dispatched == [ids[0]], (
        "the queue offered message three first; only message one may have been "
        "delivered, because the other two name predecessors that have not"
    )
    assert by_event[ids[1]]["lastErrorCode"] == WAITING_ON_PREDECESSOR
    assert by_event[ids[2]]["lastErrorCode"] == WAITING_ON_PREDECESSOR
    assert by_event[ids[1]]["status"] == "RETRY"

    # And the hold is a wait, not a refusal: once time passes the rest of the
    # chain drains, in chain order.
    for _ in range(12):
        for document in outbox.documents.values():
            if document["status"] in ("PENDING", "RETRY"):
                document["nextAttemptAt"] = base - timedelta(seconds=60)
        if not await worker.dispatch_once():
            break

    assert dispatcher.dispatched == ids


# --------------------------------------------------------------------------- #
# Concurrent arrival: the enqueuing half of acceptance 18
# --------------------------------------------------------------------------- #
#
# The sequential test above proves the *dispatcher* honours a well-formed
# chain. It cannot prove the store always produces one, because recording three
# messages one after another never has two enqueues in flight -- and a fork is
# only expressible when two are. Contracts sect. 7 assigns the population of
# `causation_id` / `required_predecessor_ids[]` to the enqueuing store, so that
# is the half these two tests cover.
#
# What the doubles below model, and why each half is faithful:
#
#   * `_interleave_reads` makes every collection call yield to the event loop
#     first. A store method that is pure in-memory never context-switches, so
#     without this two `asyncio.gather`ed calls run to completion one after the
#     other and no interleaving is possible -- the double would be proving that
#     concurrency does not happen rather than what happens under it. Real
#     collection calls are network round trips and yield at every one.
#
#   * `_TransactionalClient` serialises `with_transaction`. That is the
#     observable consequence of what MongoDB actually does here: the sequence
#     `$inc` inside the transaction makes two concurrent enqueues conflict on
#     the counter document, one aborts, and `with_transaction` re-runs it
#     against state that now includes the winner. A lock reaches the same end
#     state -- the second body runs after the first has committed -- without
#     modelling the abort/retry machinery itself.
#
# Neither double weakens the test: with the tail read moved back outside the
# transaction, both tests fail (see the ledger's fault-injection table).


class _TransactionalClient(FakeClient):
    """A `FakeClient` whose transactions are serialised, as Mongo's are here."""

    def __init__(self) -> None:
        super().__init__()
        self.transaction_lock = asyncio.Lock()

    def start_session(self) -> Any:
        client = self

        class _SerializedSession(FakeSession):
            async def with_transaction(self, callback: Any) -> Any:
                async with client.transaction_lock:
                    return await FakeSession.with_transaction(self, callback)

        session = _SerializedSession(self)

        class _Context:
            async def __aenter__(self) -> Any:
                return session

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Context()


@pytest.fixture
def interleaved_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every collection call a place the event loop may switch."""
    for name in ("find_one", "insert_one", "find_one_and_update", "update_one"):
        original = getattr(FakeCollection, name)

        def yielding(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            async def run() -> Any:
                await asyncio.sleep(0)
                return await _original(*args, **kwargs)

            return run()

        monkeypatch.setattr(FakeCollection, name, yielding)


@pytest_asyncio.fixture
async def concurrent_store(
    test_settings: Settings,
) -> tuple[DurableSupportIngressStore, FakeCollection]:
    client = _TransactionalClient()
    database = client[test_settings.mongo_database]
    store = DurableSupportIngressStore(cast(Any, client), test_settings, _configuration())
    await store.ensure_indexes()
    await ensure_integration_outbox_indexes(database)
    return store, database["integration_outbox"]


def _assert_is_a_chain(chain: list[tuple[str, tuple[str, ...]]]) -> None:
    """Every link names the one before it, and no id is named twice.

    Stated as two properties rather than as an expected list, because a fork is
    precisely the shape an expected-list assertion written from a sequential
    run would still accept: the *sequence numbers* are distinct and correct in
    both cases, and only the predecessors differ.
    """
    ids = [event_id for event_id, _ in chain]
    predecessors = [names for _, names in chain]

    assert predecessors[0] == (), "the first link on a case has no predecessor"
    for position in range(1, len(chain)):
        assert predecessors[position] == (ids[position - 1],), (
            f"event at stream position {position} names {predecessors[position]}, "
            f"but the event before it is {ids[position - 1]!r} -- this is a fork, "
            "not a chain"
        )
    named = [names[0] for names in predecessors if names]
    assert len(named) == len(set(named)), (
        f"two events name the same predecessor ({named}): they sit at the same "
        "depth, so once it delivers both become dispatchable at once and the "
        "dispatcher may run them in either order"
    )


@pytest.mark.asyncio
async def test_two_messages_arriving_together_still_form_a_chain(
    concurrent_store: tuple[DurableSupportIngressStore, FakeCollection],
    interleaved_reads: None,
) -> None:
    """Contracts sect. 7's enqueuing half, under the arrival that breaks it.

    A burst on one case is ordinary -- a transport flushing a queue, an agent
    sending two lines, a redelivery overlapping a fresh arrival. With the chain
    tail resolved outside the transaction, both of these read the same tail and
    both name it, and the result is two events at one depth.
    """
    store, outbox = concurrent_store

    first = await _record(store, _event(external_message_id="m-1"))
    second, third = await asyncio.gather(
        _record(store, _event(external_message_id="m-2")),
        _record(store, _event(external_message_id="m-3")),
    )
    assert len({first.support_event_id, second.support_event_id, third.support_event_id}) == 3

    chain = inbound_chain(list(outbox.documents.values()))
    assert len(chain) == 3
    _assert_is_a_chain(chain)


@pytest.mark.asyncio
async def test_a_chain_built_under_concurrent_arrival_still_drains_in_order(
    concurrent_store: tuple[DurableSupportIngressStore, FakeCollection],
    interleaved_reads: None,
    test_settings: Settings,
) -> None:
    """The end-to-end consequence, through S2's real dispatcher.

    The chain assertion above is the cause; this is the effect, and it is the
    one acceptance 18 is actually written about. The queue is loaded against
    the answer exactly as the sequential test loads it -- newest command made
    oldest-due -- so an unordered dispatcher delivers backwards. Delivery order
    must equal chain order, whichever way the two concurrent arrivals happened
    to be sequenced.
    """
    store, outbox = concurrent_store

    await _record(store, _event(external_message_id="m-1"))
    await asyncio.gather(
        _record(store, _event(external_message_id="m-2")),
        _record(store, _event(external_message_id="m-3")),
    )

    chain = inbound_chain(list(outbox.documents.values()))
    _assert_is_a_chain(chain)
    chain_order = [event_id for event_id, _ in chain]

    dispatcher = _RecordingDispatcher()
    worker = IntegrationOutboxDispatcher(
        cast(Any, store._client),  # noqa: SLF001 - the client the store committed through
        test_settings,
        {SUPPORT_MESSAGE_CLASSIFY_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )

    by_event = {str(document["eventId"]): document for document in outbox.documents.values()}

    def load_against_the_answer() -> None:
        """Make the newest waiting command the oldest-due -- on *every* pass.

        `claim` sorts by `(nextAttemptAt, createdAt)`, so giving every waiting
        command the same `nextAttemptAt` hands the tie-break to `createdAt` --
        which is enqueue order, which is the answer. Re-staggering each round is
        what keeps the queue adversarial for the whole drain instead of only
        the first pass; a deferred command comes back with `now + 2s`, so
        without this the queue quietly sorts itself into the right order.
        """
        now = datetime.now(UTC)
        for offset, event_id in enumerate(reversed(chain_order)):
            document = by_event[event_id]
            if document["status"] in ("PENDING", "RETRY"):
                document["nextAttemptAt"] = now - timedelta(seconds=30 - offset * 10)
                document["leaseOwner"] = None
                document["leaseUntil"] = None

    load_against_the_answer()
    for _ in range(20):
        if not await worker.dispatch_once():
            load_against_the_answer()
            if not await worker.dispatch_once():
                break

    assert dispatcher.dispatched == chain_order, (
        "the queue offered the newest command first; delivery order must still "
        "be chain order, and a forked chain is exactly what lets it not be"
    )
