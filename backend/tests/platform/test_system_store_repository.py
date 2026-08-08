"""SystemStore: logical-name resolution and the plaintext-write refusal for structures
declared encrypted=true (implementation plan Phase 3; design doc §13.6)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.encryption import PlaintextWriteRejected
from return_platform.platform.system_store.repository import SystemStore, UnknownStructure


@dataclass(frozen=True)
class _Structure:
    physical_name: str
    encrypted: bool = False


@pytest.mark.asyncio
async def test_collection_resolves_logical_name_to_physical_collection(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    store = SystemStore(
        client,
        {"ai_interceptions": _Structure(physical_name="platform_ai_interceptions")},
        database="platform",
    )

    collection = store.collection("ai_interceptions")

    assert collection.name == "platform_ai_interceptions"


def test_unknown_logical_name_is_rejected(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    store = SystemStore(client, {}, database="platform")

    with pytest.raises(UnknownStructure):
        store.collection("does_not_exist")


@pytest.mark.asyncio
async def test_insert_one_rejects_a_plaintext_write_to_an_encrypted_structure(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name="platform_ai_traces", encrypted=True)},
        database="platform",
    )

    with pytest.raises(PlaintextWriteRejected):
        await store.insert_one("ai_traces", {"prompt": "plaintext, never allowed"})


@pytest.mark.asyncio
async def test_insert_one_allows_an_envelope_wrapped_write_to_an_encrypted_structure(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    collection_name = "platform_ai_traces_envelope_probe"
    await client.get_database("platform").get_collection(collection_name).delete_many({})
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name=collection_name, encrypted=True)},
        database="platform",
    )

    await store.insert_one("ai_traces", {"_envelope": {"ciphertext": b"...", "key_id": "k1"}})

    assert (
        await client.get_database("platform").get_collection(collection_name).find_one() is not None
    )
