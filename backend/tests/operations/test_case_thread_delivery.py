"""S2: one delivery reaches the case thread exactly once.

Contracts.md sect. 7. The sending side is effectively-once -- it can retry a
send whose outcome it never learned -- so the guarantee has to be kept on the
receiver, and the observable form of it is a unique partial index on
`businessPayload.deliveryId` in `support_messages`. These tests pin that index,
the kind-agnostic post that honours it, and the thread ensure whose loser
re-reads the winner's thread instead of opening a second one.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.operations.return_support.service import (
    SUPPORT_MESSAGE_DELIVERY_INDEX,
    ReturnSupportService,
    SupportMessageType,
)
from tests.operations.mongo_double import FakeClient, FakeCollection

CASE_ID = "case-9200"
TENANT_ID = "tenant-1"
PRINCIPAL_ID = "associate-1"
DRAFT = "Please issue an RMA for the two damaged items on this return."
DELIVERY_ID = "delivery-abc-123"


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def messages(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database]["support_messages"]


@pytest.fixture
def work_items(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database]["support_work_items"]


@pytest_asyncio.fixture
async def service(
    mongo: FakeClient,
    test_settings: Settings,
    shipped_configuration: ReturnPlatformConfiguration,
) -> ReturnSupportService:
    built = ReturnSupportService(
        client=cast(AsyncMongoClient[Any], mongo),
        settings=test_settings,
        configuration=shipped_configuration,
        # No writer below reaches the repository: these are thread and message
        # writes, and the case projection is somebody else's transaction.
        operational_repository=cast(Any, None),
    )
    await built.ensure_indexes()
    return built


async def _thread(service: ReturnSupportService, *, case_id: str = CASE_ID) -> Any:
    return await service.ensure_case_support_thread(
        case_id=case_id,
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        support_draft=DRAFT,
        idempotency_key=f"case-thread:{case_id}",
    )


# --------------------------------------------------------------------------- #
# The index itself
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_receiver_dedupe_index_is_unique_and_partial_on_the_delivery_id(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """Unique, and partial on presence.

    Left unconditional, every message without a delivery id would index as
    `null` and the second ordinary comment on any thread would collide with the
    first -- the failure mode the `sessionId` index in this same method was
    already fixed for once.
    """
    del service
    declaration = next(
        options
        for _keys, options in messages.index_calls
        if options.get("name") == SUPPORT_MESSAGE_DELIVERY_INDEX
    )
    assert declaration["unique"] is True
    assert declaration["partialFilterExpression"] == {
        "businessPayload.deliveryId": {"$type": "string"}
    }


@pytest.mark.asyncio
async def test_ordinary_messages_without_a_delivery_id_do_not_collide(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """The partial filter, proven rather than asserted."""
    thread = await _thread(service)

    for text in ("First note.", "Second note."):
        await service.post_support_message(work_item_id=thread.workItemId, message_text=text)

    posted = [
        document
        for document in messages.documents.values()
        if document["messageType"] == SupportMessageType.COMMENT.value
    ]
    assert len(posted) == 2


# --------------------------------------------------------------------------- #
# Thread ensure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ensuring_the_thread_twice_yields_one_thread(
    service: ReturnSupportService, work_items: FakeCollection
) -> None:
    """One Returns Support conversation per return; a second splits it in two."""
    first = await _thread(service)
    second = await _thread(service)

    assert first.workItemId == second.workItemId
    assert first.threadId == second.threadId
    assert first.created is True
    assert second.created is False
    assert len(work_items.documents) == 1


@pytest.mark.asyncio
async def test_the_race_between_two_creators_leaves_one_thread(
    service: ReturnSupportService, work_items: FakeCollection
) -> None:
    """The loser re-reads the winner's thread rather than opening its own.

    The read-then-insert check cannot see a concurrent creator; the unique
    `caseId` index can, and this drives that path by blinding the idempotency
    pre-read so the loser's insert actually reaches the index.

    **The blind count is a magic number tied to production internals, so this
    test verifies its own injection rather than trusting it.** The number is
    the count of `caseId` reads before the insert, and it already moved 2 -> 1
    when a redundant read was removed. At 0 nothing is blinded: the pre-read
    finds the winner, the insert is never attempted, the index is never
    reached, and every outcome assertion below still holds -- because the
    sequential path satisfies them just as well, and the test silently decays
    into a copy of `test_ensuring_the_thread_twice_yields_one_thread`. Since
    this is the only guard on `created` being decided by the write, a silent
    decay would leave that unguarded. So the assertions at the end check that
    the race was *constructed*: the blind fired exactly once, and the loser's
    insert was actually rejected by the unique index.

    (Upward the number is self-limiting for a different reason: at 2 the
    loser's re-read of the winner is blinded too and the code re-raises rather
    than converging. That direction was always caught. Downward was not, which
    is what these assertions fix.)
    """
    winner = await _thread(service)

    original_find_one = work_items.find_one
    original_insert_one = work_items.insert_one
    blinded = {"count": 0}
    rejected_by_index = {"count": 0}

    async def blind_once(query: Any, **kwargs: Any) -> Any:
        if blinded["count"] < 1 and "caseId" in str(query):
            blinded["count"] += 1
            return None
        return await original_find_one(query, **kwargs)

    async def recording_insert(document: Any, **kwargs: Any) -> Any:
        try:
            return await original_insert_one(document, **kwargs)
        except DuplicateKeyError:
            # The unique `caseId` index catching the race. Counted, not
            # swallowed: the service's own handler is what must see it.
            rejected_by_index["count"] += 1
            raise

    work_items.find_one = blind_once  # type: ignore[method-assign]
    work_items.insert_one = recording_insert  # type: ignore[method-assign]
    try:
        loser = await _thread(service)
    finally:
        work_items.find_one = original_find_one  # type: ignore[method-assign]
        work_items.insert_one = original_insert_one  # type: ignore[method-assign]

    # The race was actually constructed. Without these two, everything below
    # passes on the ordinary sequential path and this test guards nothing.
    assert blinded["count"] == 1, "the pre-read blind never fired: no race was constructed"
    assert rejected_by_index["count"] == 1, (
        "the loser's insert never reached the unique index: the DuplicateKeyError "
        "path this test exists for did not execute"
    )

    assert loser.workItemId == winner.workItemId
    assert loser.threadId == winner.threadId
    assert len(work_items.documents) == 1
    # The field the race can actually corrupt, and the one this test used to
    # leave unasserted. `created` decides between composing an opening request
    # and composing a reply: a loser reporting `True` would put a second
    # opening message on Support, through the one field the delivery identity
    # does not cover.
    assert winner.created is True
    assert loser.created is False


# --------------------------------------------------------------------------- #
# Receiver dedupe
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_redelivered_message_is_absorbed_not_duplicated(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """The whole point: one message on B, however many sends reached it.

    The cost of getting this wrong is not a duplicate row -- it is the same
    request arriving twice in a person's queue, and them answering it twice.
    """
    thread = await _thread(service)
    before = len(messages.documents)

    first = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="Approved template body.",
        delivery_id=DELIVERY_ID,
    )
    second = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="Approved template body.",
        delivery_id=DELIVERY_ID,
    )

    assert first.absorbed is False
    assert second.absorbed is True
    assert second.messageId == first.messageId
    assert second.sequence == first.sequence
    assert second.threadId == first.threadId
    assert len(messages.documents) == before + 1


@pytest.mark.asyncio
async def test_the_index_absorbs_the_duplicate_the_pre_check_could_not_see(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """The second of the two checks, on its own.

    Two workflow workers replaying the same send at the same instant both pass
    the read; only the index stands between that and two messages.
    """
    thread = await _thread(service)
    first = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="Approved template body.",
        delivery_id=DELIVERY_ID,
    )
    before = len(messages.documents)

    original_find_one = messages.find_one
    blinded = {"count": 0}

    async def blind_once(query: Any, **kwargs: Any) -> Any:
        if blinded["count"] == 0 and "businessPayload.deliveryId" in query:
            blinded["count"] += 1
            return None
        return await original_find_one(query, **kwargs)

    messages.find_one = blind_once  # type: ignore[method-assign]
    try:
        absorbed = await service.post_support_message(
            work_item_id=thread.workItemId,
            message_text="Approved template body.",
            delivery_id=DELIVERY_ID,
        )
    finally:
        messages.find_one = original_find_one  # type: ignore[method-assign]

    assert absorbed.absorbed is True
    assert absorbed.messageId == first.messageId
    assert len(messages.documents) == before


@pytest.mark.asyncio
async def test_two_different_deliveries_both_land(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """Dedupe is identity-scoped, not a rate limit."""
    thread = await _thread(service)

    first = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="The template.",
        delivery_id="delivery-1",
    )
    second = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="A later reply.",
        delivery_id="delivery-2",
    )

    assert first.absorbed is second.absorbed is False
    assert first.messageId != second.messageId
    assert second.sequence == first.sequence + 1
    stored = {
        str(document["businessPayload"].get("deliveryId"))
        for document in messages.documents.values()
        if document.get("businessPayload", {}).get("deliveryId")
    }
    assert stored == {"delivery-1", "delivery-2"}


@pytest.mark.asyncio
async def test_the_delivery_id_cannot_be_displaced_by_the_business_payload(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """A payload that overwrote it would opt out of the guarantee silently."""
    thread = await _thread(service)

    posted = await service.post_support_message(
        work_item_id=thread.workItemId,
        message_text="The template.",
        delivery_id=DELIVERY_ID,
        business_payload={"deliveryId": "something-else", "reviewId": "rev-1"},
    )

    document = messages.documents[posted.messageId]
    assert document["businessPayload"]["deliveryId"] == DELIVERY_ID
    assert document["businessPayload"]["reviewId"] == "rev-1"


@pytest.mark.asyncio
async def test_the_post_is_kind_agnostic(
    service: ReturnSupportService, messages: FakeCollection
) -> None:
    """One path for template, reply and clarification (contracts.md sect. 7).

    Each getting its own posting path would give each its own chance to get
    the dedupe wrong.
    """
    thread = await _thread(service)

    for index, message_type in enumerate((SupportMessageType.COMMENT, SupportMessageType.REQUEST)):
        posted = await service.post_support_message(
            work_item_id=thread.workItemId,
            message_text=f"Body {index}.",
            delivery_id=f"delivery-{index}",
            message_type=message_type,
        )
        assert messages.documents[posted.messageId]["messageType"] == message_type.value


@pytest.mark.asyncio
async def test_posting_to_an_unknown_work_item_raises(
    service: ReturnSupportService,
) -> None:
    with pytest.raises(KeyError):
        await service.post_support_message(work_item_id="no-such-item", message_text="Hello.")
