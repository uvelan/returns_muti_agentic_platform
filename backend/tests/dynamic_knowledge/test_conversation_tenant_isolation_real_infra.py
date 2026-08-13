"""Conversation history is per principal, enforced in the query filter.

`MongoAtomicConversationStore.list_recent` was `find({})`. Every associate's
history was returned to every caller, and a transcript carries whatever the
associate typed about a customer -- names, addresses, phone numbers.

The fix has to hold against a *guessed id*, not just against the list endpoint.
A conversation id is a client-generated UUID that travels in a URL, so the
interesting attack is not "list someone else's conversations", it is "read one
you happen to know the id of". Both are asserted below.

Run against real MongoDB deliberately. The property under test is that a Mongo
query filter cannot match another owner's document; an in-memory fake would be
asserting that the fake implements the filter, which proves nothing.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoAtomicConversationStore,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
    ConversationScope,
)

pytestmark = pytest.mark.asyncio


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin&directConnection=true"


ALICE = ConversationScope(tenant_id="tenant-a", principal_id="alice")
# Same tenant, different person: isolation is per principal, not only per tenant.
BOB = ConversationScope(tenant_id="tenant-a", principal_id="bob")
# Same *principal id*, different tenant: a colliding subject across tenants must
# not see across the boundary either.
CARLA = ConversationScope(tenant_id="tenant-b", principal_id="alice")


def _document(conversation_id: str, text: str) -> dict[str, Any]:
    return {
        "conversationId": conversation_id,
        "version": 0,
        "graphGenerationId": "gen-test",
        "turns": {},
        "state": {"transcript": [{"role": "associate", "text": text}]},
        "updatedAt": datetime.now(UTC),
    }


@pytest_asyncio.fixture
async def store() -> Any:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    collection = f"conversation_isolation_test_{uuid.uuid4().hex[:12]}"
    made = MongoAtomicConversationStore(client, "return_platform", collection=collection)
    await made.ensure_indexes()
    try:
        yield made
    finally:
        await client.get_database("return_platform").drop_collection(collection)
        await client.close()


async def _seed(store: MongoAtomicConversationStore, scope: ConversationScope, text: str) -> str:
    conversation_id = str(uuid.uuid4())
    committed = await store.compare_and_set(
        conversation_id=conversation_id,
        expected_version=0,
        replacement=_document(conversation_id, text),
        scope=scope,
    )
    assert committed
    return conversation_id


async def test_history_lists_only_the_callers_own_conversations(
    store: MongoAtomicConversationStore,
) -> None:
    await _seed(store, ALICE, "alice looking for CW273354")
    await _seed(store, BOB, "bob looking for a different order")
    await _seed(store, CARLA, "same subject id, other tenant")

    repository = AtomicConversationRepository(store)

    alice_titles = [row.title for row in await repository.list_recent(scope=ALICE)]
    assert alice_titles == ["alice looking for CW273354"]

    bob_titles = [row.title for row in await repository.list_recent(scope=BOB)]
    assert bob_titles == ["bob looking for a different order"]

    carla_titles = [row.title for row in await repository.list_recent(scope=CARLA)]
    assert carla_titles == ["same subject id, other tenant"]


async def test_a_guessed_conversation_id_does_not_bypass_authorization(
    store: MongoAtomicConversationStore,
) -> None:
    """The id is known to the attacker. Knowing it must still not be enough."""
    alice_conversation = await _seed(store, ALICE, "alice's customer said 555-0100")

    repository = AtomicConversationRepository(store)

    assert await repository.read_transcript(alice_conversation, scope=ALICE) is not None
    # Same tenant, different principal.
    assert await repository.read_transcript(alice_conversation, scope=BOB) is None
    # Same principal id, different tenant.
    assert await repository.read_transcript(alice_conversation, scope=CARLA) is None


async def test_another_principal_cannot_overwrite_a_conversation_it_cannot_read(
    store: MongoAtomicConversationStore,
) -> None:
    """Write isolation, not only read isolation.

    Without the scope in the update filter, a caller who guessed the id *and*
    the version could replace the document -- destroying a conversation they
    were never able to see.
    """
    alice_conversation = await _seed(store, ALICE, "original")

    hijacked = await store.compare_and_set(
        conversation_id=alice_conversation,
        expected_version=0,
        replacement=_document(alice_conversation, "overwritten by bob"),
        scope=BOB,
    )
    assert hijacked is False

    surviving = await store.read(alice_conversation, scope=ALICE)
    assert surviving is not None
    assert surviving["state"]["transcript"][0]["text"] == "original"


async def test_a_committed_conversation_is_stamped_with_its_owner(
    store: MongoAtomicConversationStore,
) -> None:
    """An unstamped document matches no scoped filter, so it would be invisible
    to the associate who created it. The store must stamp, not the caller."""
    conversation_id = await _seed(store, ALICE, "stamped on create")

    document = await store.read(conversation_id, scope=ALICE)

    assert document is not None
    assert document["tenantId"] == ALICE.tenant_id
    assert document["principalId"] == ALICE.principal_id
