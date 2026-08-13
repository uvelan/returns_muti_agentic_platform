"""`RepositoryCaseStore`: confirmation is idempotent, against real MongoDB.

The unit-level smoke net models idempotency in a fake, which proves the node
uses it. This proves the real thing does it -- and specifically that it holds
when two confirmations race, where the fake's dict cannot fail and a unique
index can.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.order_agent.contracts import OrderConfirmation
from return_platform.operations.repository import OperationalRepository

pytestmark = pytest.mark.asyncio(loop_scope="module")


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


TENANT = "tenant-a"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context() -> Any:
    database = f"case_store_test_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    repository = OperationalRepository(client, settings)
    await repository.ensure_indexes()
    try:
        yield RepositoryCaseStore(repository), repository
    finally:
        await client.drop_database(database)
        await client.close()


def _confirmation(
    order: str = "CW273354", lines: tuple[str, ...] = ("L1", "L2")
) -> OrderConfirmation:
    return OrderConfirmation(
        candidate_set_id="cs-1",
        candidate_id="cand-1",
        order_reference=order,
        order_line_references=lines,
    )


async def _confirm(store: RepositoryCaseStore, conversation: str, **kwargs: Any) -> Any:
    return await store.confirm_case(
        tenant_id=TENANT,
        principal_id="associate-1",
        branch_ids=("CHARLOTTE",),
        conversation_id=conversation,
        confirmation=kwargs.pop("confirmation", _confirmation()),
        configuration_release_id="release-1",
        graph_generation_id="gen-1",
        **kwargs,
    )


async def test_repeating_a_confirmation_returns_the_same_case(context: Any) -> None:
    store, _ = context
    conversation = str(uuid.uuid4())

    first = await _confirm(store, conversation)
    second = await _confirm(store, conversation)

    assert first.case_id == second.case_id
    assert first.already_existed is False
    assert second.already_existed is True


async def test_two_simultaneous_confirmations_produce_one_case(context: Any) -> None:
    """The race the unique index exists for.

    Both callers pass the find-then-create check before either inserts, so one
    insert loses on the index and must read back the winner rather than raising
    or creating a second case.
    """
    store, repository = context
    conversation = str(uuid.uuid4())

    results = await asyncio.gather(_confirm(store, conversation), _confirm(store, conversation))

    assert len({result.case_id for result in results}) == 1
    matching = [
        case
        for case in await repository.list_cases_for_principal(
            tenant_id=TENANT, principal_id="associate-1"
        )
        if case["channelAConversationId"] == conversation
    ]
    assert len(matching) == 1


async def test_a_different_order_on_one_conversation_is_a_different_case(
    context: Any,
) -> None:
    """Two orders confirmed in one conversation are two returns.

    Keying idempotency on the conversation alone would silently discard the
    second.
    """
    store, _ = context
    conversation = str(uuid.uuid4())

    first = await _confirm(store, conversation)
    second = await _confirm(store, conversation, confirmation=_confirmation(order="CW999999"))

    assert first.case_id != second.case_id


async def test_a_different_line_set_is_a_different_case(context: Any) -> None:
    """A partial return of two lines is not the same intent as one of three."""
    store, _ = context
    conversation = str(uuid.uuid4())

    first = await _confirm(store, conversation)
    second = await _confirm(
        store, conversation, confirmation=_confirmation(lines=("L1", "L2", "L3"))
    )

    assert first.case_id != second.case_id


async def test_line_order_does_not_change_the_identity_of_a_confirmation(
    context: Any,
) -> None:
    """The model listing lines in a different order on a retry is still a retry."""
    store, _ = context
    conversation = str(uuid.uuid4())

    first = await _confirm(store, conversation, confirmation=_confirmation(lines=("L1", "L2")))
    second = await _confirm(store, conversation, confirmation=_confirmation(lines=("L2", "L1")))

    assert first.case_id == second.case_id
    assert second.already_existed is True


async def test_confirming_records_the_order_as_a_provenanced_fact(context: Any) -> None:
    """Everything downstream reads the fact, not the conversation."""
    store, repository = context
    conversation = str(uuid.uuid4())

    confirmed = await _confirm(store, conversation)

    latest = await repository.latest_case_facts(confirmed.case_id)
    assert latest["confirmed_order_reference"]["value"] == "CW273354"
    assert latest["confirmed_order_reference"]["acquisitionMethod"] == "STATED"
    assert latest["confirmed_order_reference"]["channel"] == "CHANNEL_A"
    assert latest["confirmed_order_lines"]["value"] == ["L1", "L2"]


async def test_a_retried_confirmation_does_not_duplicate_the_fact(context: Any) -> None:
    store, repository = context
    conversation = str(uuid.uuid4())

    confirmed = await _confirm(store, conversation)
    await _confirm(store, conversation)

    facts = await repository.list_case_facts(confirmed.case_id)
    order_facts = [fact for fact in facts if fact["factName"] == "confirmed_order_reference"]
    assert len(order_facts) == 1


async def test_an_ambiguous_branch_is_left_unset_rather_than_guessed(context: Any) -> None:
    """A principal scoped to several branches has not said which this is for."""
    store, repository = context
    conversation = str(uuid.uuid4())

    confirmed = await store.confirm_case(
        tenant_id=TENANT,
        principal_id="associate-multi",
        branch_ids=("CHARLOTTE", "RALEIGH"),
        conversation_id=conversation,
        confirmation=_confirmation(),
        configuration_release_id="release-1",
        graph_generation_id="gen-1",
    )

    case = await repository.get_case(confirmed.case_id)
    assert case is not None
    assert case["branchId"] is None
