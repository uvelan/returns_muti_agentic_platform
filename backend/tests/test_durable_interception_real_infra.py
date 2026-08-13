"""The interception store's two claims that only a database can settle.

**Sealing.** `ai_interceptions` is declared `encrypted: true`, so the store layer
itself must refuse a plaintext write and the raw document must not contain the
prompt. A dict double accepts anything, so it can prove neither.

**Compare-and-set on answer.** Two operators answering the same held request is
a real scenario. The loser must be told, not silently overwritten. An in-memory
double's `if status == PENDING` is a different mechanism with different race
behaviour than a filtered Mongo write.

Bootstraps a uniquely-suffixed copy of the real manifest so a concurrent process
using the production collection names is unaffected, and avoids the shared
`test_settings` fixture, which demands API keys nothing here exercises.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.ai.interception.dispatcher import (
    RESUME_COMMANDS,
    InterceptionResumeDispatcher,
    resume_command_id,
)
from return_platform.ai.interception.records import InterceptionStatus, ResumeCommand
from return_platform.ai.interception.store import (
    AI_INTERCEPTIONS,
    METADATA_FIELDS,
    InterceptionNotPending,
    SystemStoreInterceptionStore,
)
from return_platform.configuration.settings import (
    DEFAULT_SYSTEM_STORE_MANIFEST_PATH,
    DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64,
)
from return_platform.platform.secrets.envelope import AesGcmEnvelopeEncryptor
from return_platform.platform.system_store.bootstrap import SystemStoreBootstrapper
from return_platform.platform.system_store.manifest_loader import (
    load_system_store_config,
    structure_definitions,
)
from return_platform.platform.system_store.migrations import MigrationRunner
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoBootstrapStateStore,
    MongoLeaseStore,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
)
from return_platform.platform.system_store.repository import SystemStore

SECRET_PROMPT = "customer@example.com placed order ORD-99 for a widget"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin&directConnection=true"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def system_store() -> AsyncIterator[SystemStore]:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    config = load_system_store_config(DEFAULT_SYSTEM_STORE_MANIFEST_PATH)
    suffix = uuid.uuid4().hex[:12]
    structures = tuple(
        definition.model_copy(update={"physical_name": f"{definition.physical_name}_t_{suffix}"})
        for definition in structure_definitions(config)
    )
    bootstrapper = SystemStoreBootstrapper(
        lease_store=MongoLeaseStore(client, database="platform"),
        adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
        migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
        bootstrap_state=MongoBootstrapStateStore(client, database="platform"),
        guard=FencedMongoTransactionGuard(client, database="platform"),
        owner_instance_id=f"interception-test-{suffix}",
        fail_closed_on_drift=config.fail_closed_on_drift,
    )
    await bootstrapper.bootstrap(
        list(structures), auto_bootstrap_missing=config.auto_bootstrap_missing_structures
    )
    store = SystemStore(
        client,
        {definition.logical_name: definition for definition in structures},
        database="platform",
    )
    try:
        yield store
    finally:
        database = client.get_database("platform")
        for definition in structures:
            await database.drop_collection(definition.physical_name)
        await client.close()


def _encryptor() -> AesGcmEnvelopeEncryptor:
    return AesGcmEnvelopeEncryptor(
        key=base64.b64decode(DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64), key_ref="test-key"
    )


def _resume() -> ResumeCommand:
    return ResumeCommand(run_id="run-1", thread_id="thread-1", workflow_id="wf-1")


async def _open(store: SystemStoreInterceptionStore, interception_id: str) -> None:
    await store.open(
        interception_id=interception_id,
        task_id="ORDER_AGENT_REASONING_V1",
        request_payload={"systemPrompt": SECRET_PROMPT, "userPayload": {"mode": "DECIDE"}},
        resume=_resume(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_a_held_request_round_trips(system_store: SystemStore) -> None:
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"

    await _open(store, interception_id)
    record = await store.get(interception_id)

    assert record is not None
    assert record.status is InterceptionStatus.PENDING
    # The resume command survives the round trip: a worker picking this up later
    # is the entire reason it is stored rather than derived.
    assert record.resume == _resume()
    payload = await store.request_payload(interception_id)
    assert payload is not None and payload["systemPrompt"] == SECRET_PROMPT


@pytest.mark.asyncio(loop_scope="module")
async def test_the_prompt_is_not_readable_in_the_raw_document(
    system_store: SystemStore,
) -> None:
    """The point of the encrypted structure. A held request carries whatever the
    prompt carried -- for the analyzer, sampled customer rows."""
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)

    raw = await system_store.read_only(AI_INTERCEPTIONS).find_one(
        {"interception_id": interception_id}
    )

    assert raw is not None
    assert SECRET_PROMPT not in str(raw)
    assert set(raw["_envelope"]) == {"ciphertext", "key_ref", "algorithm", "version"}
    # Metadata an operator console filters on stays queryable in the clear.
    assert raw["task_id"] == "ORDER_AGENT_REASONING_V1"
    assert raw["status"] == InterceptionStatus.PENDING.value


@pytest.mark.asyncio(loop_scope="module")
async def test_the_store_refuses_a_plaintext_write(system_store: SystemStore) -> None:
    """Not the repository's discipline -- the store's. This is what makes "a
    held prompt is never persisted in the clear" a property rather than a
    habit."""
    with pytest.raises(Exception) as caught:
        await system_store.insert_one(
            AI_INTERCEPTIONS,
            {"interception_id": "plain-1", "systemPrompt": SECRET_PROMPT},
            allowed_metadata_fields=frozenset({"interception_id"}),
        )
    assert "envelope" in str(caught.value).lower() or "encrypt" in str(caught.value).lower()


@pytest.mark.asyncio(loop_scope="module")
async def test_answering_records_the_human_and_seals_their_reply(
    system_store: SystemStore,
) -> None:
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)

    reply = '{"decision": "APPROVE", "note": "customer@example.com confirmed"}'
    answered = await store.answer(
        interception_id=interception_id, response_text=reply, answered_by="alex@example.com"
    )

    assert answered.status is InterceptionStatus.ANSWERED
    assert answered.answered_by == "alex@example.com"
    raw = await system_store.read_only(AI_INTERCEPTIONS).find_one(
        {"interception_id": interception_id}
    )
    assert raw is not None
    # A human's reply to a prompt containing customer data is itself likely to
    # contain customer data; sealing one and not the other would be theatre.
    assert "customer@example.com" not in str(raw)
    payload = await store.request_payload(interception_id)
    assert payload is not None and payload["responseText"] == reply


@pytest.mark.asyncio(loop_scope="module")
async def test_two_operators_answering_at_once_produce_one_winner(
    system_store: SystemStore,
) -> None:
    """The compare-and-set. Without the PENDING filter the second write would
    silently discard the first operator's text, and neither would ever know."""
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)

    results = await asyncio.gather(
        store.answer(interception_id=interception_id, response_text="first", answered_by="op-1"),
        store.answer(interception_id=interception_id, response_text="second", answered_by="op-2"),
        return_exceptions=True,
    )

    succeeded = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, InterceptionNotPending)]
    assert len(succeeded) == 1, "exactly one operator must win"
    assert len(refused) == 1, "the loser must be told, not silently discarded"


@pytest.mark.asyncio(loop_scope="module")
async def test_an_answered_interception_cannot_be_answered_again(
    system_store: SystemStore,
) -> None:
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    await store.answer(interception_id=interception_id, response_text="first", answered_by="op-1")

    with pytest.raises(InterceptionNotPending):
        await store.answer(
            interception_id=interception_id, response_text="second", answered_by="op-2"
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_cancelling_an_answered_interception_does_not_discard_the_answer(
    system_store: SystemStore,
) -> None:
    """An expiry sweep racing a late answer is exactly when this happens. The
    sweep must lose."""
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    await store.answer(
        interception_id=interception_id, response_text="the answer", answered_by="op-1"
    )

    await store.cancel(interception_id=interception_id, status=InterceptionStatus.EXPIRED)

    record = await store.get(interception_id)
    assert record is not None and record.status is InterceptionStatus.ANSWERED


# --- the operator queue ------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_the_pending_queue_lists_oldest_first_without_unsealing(
    system_store: SystemStore,
) -> None:
    """What `/api/ai/interceptions` serves.

    Oldest first because the queue is worked in arrival order and the oldest
    item is closest to expiring unanswered. The returned records carry no
    payload: decrypting every held prompt to render a list would defeat sealing
    them, and an operator scanning the queue needs identity and age, not
    content.
    """
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    first = f"i-{uuid.uuid4().hex[:8]}"
    second = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, first)
    await asyncio.sleep(0.01)
    await _open(store, second)

    pending = await store.list_pending()
    ids = [record.interception_id for record in pending]

    assert ids.index(first) < ids.index(second), "queue must be oldest first"
    listed = next(r for r in pending if r.interception_id == first)
    assert listed.response_text is None, "listing must not carry payload content"
    assert listed.task_id == "ORDER_AGENT_REASONING_V1"


@pytest.mark.asyncio(loop_scope="module")
async def test_an_answered_interception_leaves_the_queue(system_store: SystemStore) -> None:
    """Otherwise an operator is shown work someone else already did."""
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    assert any(r.interception_id == interception_id for r in await store.list_pending())

    await store.answer(interception_id=interception_id, response_text="done", answered_by="op-1")

    assert not any(r.interception_id == interception_id for r in await store.list_pending())


# --- the resume bridge -------------------------------------------------------


async def _assert_command_index(system_store: SystemStore) -> None:
    """The unique index is the correctness mechanism, so assert it exists.

    An earlier version of this helper *created* the index, which failed against
    a bootstrapped store: `system_store.yaml` already declares
    `command_id_unique` on `reasoning_resume_commands`, and a second unnamed
    index on the same key is an IndexOptionsConflict. Asserting rather than
    creating also keeps the manifest the single source of provisioning -- a test
    that creates its own indexes can pass while production lacks them.
    """
    names = await system_store.collection(RESUME_COMMANDS).index_information()
    unique_on_command_id = [
        name
        for name, spec in names.items()
        if spec.get("unique") and [k for k, _ in spec.get("key", [])] == ["command_id"]
    ]
    assert unique_on_command_id, (
        "reasoning_resume_commands must carry a unique index on command_id -- it is what "
        "makes a replayed enqueue collide instead of delivering a second signal"
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_an_answered_interception_becomes_a_resume_command(
    system_store: SystemStore,
) -> None:
    await _assert_command_index(system_store)
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    dispatcher = InterceptionResumeDispatcher(system_store)
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    await store.answer(interception_id=interception_id, response_text="{}", answered_by="op-1")

    assert await dispatcher.dispatch_once() >= 1

    command = await system_store.read_only(RESUME_COMMANDS).find_one(
        {"command_id": resume_command_id(interception_id)}
    )
    assert command is not None
    assert command["status"] == "PENDING"
    # The resume command stored with the interception is what reaches the worker.
    assert command["workflow_id"] == "wf-1"
    assert command["run_id"] == "run-1"


@pytest.mark.asyncio(loop_scope="module")
async def test_a_pending_interception_is_not_enqueued(system_store: SystemStore) -> None:
    """Resuming work a human has not answered would feed the graph an empty
    reply and burn the turn."""
    await _assert_command_index(system_store)
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)

    await InterceptionResumeDispatcher(system_store).dispatch_once()

    assert (
        await system_store.read_only(RESUME_COMMANDS).find_one(
            {"command_id": resume_command_id(interception_id)}
        )
    ) is None


@pytest.mark.asyncio(loop_scope="module")
async def test_dispatching_twice_produces_exactly_one_command(
    system_store: SystemStore,
) -> None:
    """The at-least-once property, tested the way it actually fails.

    A crash between "wrote the command" and "stamped the interception" replays
    the enqueue. Safety comes from the unique index on the derived command id,
    not from the stamp -- so this deletes the stamp to simulate the crash and
    asserts the replay does not deliver a second signal.
    """
    await _assert_command_index(system_store)
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    dispatcher = InterceptionResumeDispatcher(system_store)
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    await store.answer(interception_id=interception_id, response_text="{}", answered_by="op-1")
    await dispatcher.dispatch_once()

    # Simulate the crash: the command exists, the stamp never landed. Rewritten
    # through the guarded store rather than a raw handle -- `ai_interceptions`
    # is encrypted, so `SystemStore.collection()` refuses it outright, which is
    # the 3R.6 hardening doing its job even against a test.
    document = await system_store.read_only(AI_INTERCEPTIONS).find_one(
        {"interception_id": interception_id}
    )
    assert document is not None
    unstamped = {k: v for k, v in document.items() if k != "resume_enqueued_at"}
    await system_store.replace_one(
        AI_INTERCEPTIONS,
        {"interception_id": interception_id},
        unstamped,
        allowed_metadata_fields=METADATA_FIELDS | {"resume_enqueued_at"},
    )
    await dispatcher.dispatch_once()

    count = await system_store.read_only(RESUME_COMMANDS).count_documents(
        {"command_id": resume_command_id(interception_id)}
    )
    assert count == 1, "a replayed enqueue must not produce a second resume command"


@pytest.mark.asyncio(loop_scope="module")
async def test_a_dispatched_interception_leaves_the_dispatch_queue(
    system_store: SystemStore,
) -> None:
    """The stamp is an optimisation, but it has to work -- otherwise every pass
    re-reads every answered interception the platform has ever had."""
    await _assert_command_index(system_store)
    store = SystemStoreInterceptionStore(system_store, _encryptor())
    dispatcher = InterceptionResumeDispatcher(system_store)
    interception_id = f"i-{uuid.uuid4().hex[:8]}"
    await _open(store, interception_id)
    await store.answer(interception_id=interception_id, response_text="{}", answered_by="op-1")
    await dispatcher.dispatch_once()

    document = await system_store.read_only(AI_INTERCEPTIONS).find_one(
        {"interception_id": interception_id}
    )
    assert document is not None and document.get("resume_enqueued_at") is not None
    # And the payload is still sealed after the stamping rewrite.
    assert SECRET_PROMPT not in str(document)
