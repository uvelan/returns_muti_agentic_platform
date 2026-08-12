"""Channel B for a case: one thread, and no duplicate nudges.

`ReturnCaseWorkflow` depends on both properties and cannot enforce either --
Temporal retries the activity with identical input, so the guarantee has to be
in the store. Asserted against real MongoDB because the mechanism *is* a unique
index and a dedup read, neither of which a fake can get wrong.

The consequence of getting it wrong is not a duplicate row. It is a second
conversation opened with a person, or the same reminder arriving twice in their
queue.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import ReturnSupportService

pytestmark = pytest.mark.asyncio(loop_scope="module")

CONFIG = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def support() -> Any:
    database = f"case_support_test_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    repository = OperationalRepository(client, settings)
    await repository.ensure_indexes()
    service = ReturnSupportService(
        client=client,
        settings=settings,
        configuration=load_return_configuration(CONFIG).configuration,
        operational_repository=repository,
    )
    await service.ensure_indexes()
    try:
        yield service
    finally:
        await client.drop_database(database)
        await client.close()


async def _open(support: ReturnSupportService, case_id: str) -> str:
    return await support.open_case_thread(
        case_id=case_id,
        tenant_id="tenant-a",
        principal_id="associate-1",
        support_draft="Hello -- could you raise the RMA for this return please?",
        # The key the workflow derives from the case.
        idempotency_key=f"support:{case_id}",
    )


async def test_opening_the_same_case_thread_twice_returns_one_thread(
    support: ReturnSupportService,
) -> None:
    """A Temporal retry, or a replay after continue_as_new."""
    case_id = f"case-{uuid.uuid4().hex[:8]}"

    first = await _open(support, case_id)
    second = await _open(support, case_id)

    assert first == second


async def test_two_simultaneous_opens_produce_one_thread(
    support: ReturnSupportService,
) -> None:
    """Both callers pass the existence check before either inserts.

    The unique `caseId` index is what actually decides it; the loser reads back
    the winner rather than raising or opening a second conversation.
    """
    case_id = f"case-{uuid.uuid4().hex[:8]}"

    results = await asyncio.gather(_open(support, case_id), _open(support, case_id))

    assert len(set(results)) == 1


async def test_the_opening_message_is_on_the_thread(support: ReturnSupportService) -> None:
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    work_item_id = await _open(support, case_id)

    item = await support.get_work_item(work_item_id)
    assert item is not None
    messages = await support.list_messages(item.threadId)

    assert len(messages) == 1
    assert messages[0].senderRole == "AGENT"
    assert "RMA" in messages[0].messageText


async def test_a_repeated_reminder_is_sent_once(support: ReturnSupportService) -> None:
    """The cost of getting this wrong is a person reading the same nudge twice."""
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    work_item_id = await _open(support, case_id)
    key = f"reminder:{case_id}:1"

    await support.post_reminder(
        work_item_id=work_item_id, reminder_number=1, max_reminders=3, idempotency_key=key
    )
    await support.post_reminder(
        work_item_id=work_item_id, reminder_number=1, max_reminders=3, idempotency_key=key
    )

    item = await support.get_work_item(work_item_id)
    assert item is not None
    reminders = [
        message
        for message in await support.list_messages(item.threadId)
        if message.businessPayload.get("reminderKey") == key
    ]
    assert len(reminders) == 1


async def test_successive_reminders_are_distinct_and_land_on_the_same_thread(
    support: ReturnSupportService,
) -> None:
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    work_item_id = await _open(support, case_id)

    for number in (1, 2):
        await support.post_reminder(
            work_item_id=work_item_id,
            reminder_number=number,
            max_reminders=2,
            idempotency_key=f"reminder:{case_id}:{number}",
        )

    item = await support.get_work_item(work_item_id)
    assert item is not None
    messages = await support.list_messages(item.threadId)
    reminders = [m for m in messages if "reminderKey" in m.businessPayload]

    assert len(reminders) == 2
    # The last one asks who else to ask, because there will not be another.
    assert "point me at who to ask" in reminders[-1].messageText
    # Same thread throughout: a reminder must never open a new conversation.
    assert {message.threadId for message in messages} == {item.threadId}


async def test_a_case_thread_has_no_session(support: ReturnSupportService) -> None:
    """A case is the thing sessions hang off, not the other way round.

    `sessionId` stays unset, which is why its unique index had to become
    partial -- unconditional, the second case thread collided with the first.
    """
    first = await _open(support, f"case-{uuid.uuid4().hex[:8]}")
    second = await _open(support, f"case-{uuid.uuid4().hex[:8]}")

    assert first != second
