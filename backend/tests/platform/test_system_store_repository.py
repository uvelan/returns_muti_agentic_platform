"""SystemStore: logical-name resolution and the plaintext-write refusal for structures
declared encrypted=true (implementation plan Phase 3; design doc §13.6; Slice 3R.6).

Slice 3R.6 hardens this beyond "an `_envelope` key exists": the envelope's internal
shape is validated (`ciphertext`/`key_ref`/`algorithm`/`version`), unauthorized
plaintext fields alongside the envelope are rejected, and `collection()` refuses to
hand out an unrestricted raw handle for an encrypted structure at all -- `read_only()`
returns a genuine wrapper with no mutation methods, not the raw collection object under
a narrower type annotation."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.encryption import PlaintextWriteRejected
from return_platform.platform.system_store.repository import (
    EncryptedStructureRequiresGuardedAccess,
    SystemStore,
    UnknownStructure,
)


@dataclass(frozen=True)
class _Structure:
    physical_name: str
    encrypted: bool = False


def _valid_envelope() -> dict[str, object]:
    return {
        "_envelope": {
            "ciphertext": b"...",
            "key_ref": "k1",
            "algorithm": "AES-256-GCM",
            "version": "1",
        }
    }


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


def test_collection_refuses_a_raw_handle_for_an_encrypted_structure(
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

    with pytest.raises(EncryptedStructureRequiresGuardedAccess):
        store.collection("ai_traces")


@pytest.mark.asyncio
async def test_read_only_exposes_no_mutation_method(test_settings: Settings) -> None:
    """`read_only()` must return a genuine wrapper -- not the raw collection under a
    narrower type annotation, which would still let a caller call insert_one() on it
    directly, bypassing the guard entirely (Slice 3R.6)."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    collection_name = "platform_ai_traces_readonly_probe"
    await client.get_database("platform").get_collection(collection_name).delete_many({})
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name=collection_name, encrypted=True)},
        database="platform",
    )

    view = store.read_only("ai_traces")

    assert await view.find_one({"_id": "does-not-exist"}) is None
    for mutation_method in ("insert_one", "insert_many", "update_one", "replace_one", "delete_one"):
        assert not hasattr(view, mutation_method), (
            f"ReadOnlyCollection view must not expose {mutation_method}"
        )


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
async def test_insert_one_rejects_a_malformed_envelope(test_settings: Settings) -> None:
    """Presence of the `_envelope` key alone is not sufficient -- every required field
    must be present."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name="platform_ai_traces", encrypted=True)},
        database="platform",
    )

    with pytest.raises(PlaintextWriteRejected):
        await store.insert_one("ai_traces", {"_envelope": {"ciphertext": b"..."}})


@pytest.mark.asyncio
async def test_insert_one_rejects_unauthorized_plaintext_alongside_a_valid_envelope(
    test_settings: Settings,
) -> None:
    """`{"_envelope": {...well-formed...}, "password": "plaintext"}` must not pass just
    because the envelope itself is well-formed."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name="platform_ai_traces", encrypted=True)},
        database="platform",
    )

    document = {**_valid_envelope(), "password": "plaintext"}

    with pytest.raises(PlaintextWriteRejected):
        await store.insert_one("ai_traces", document)


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

    await store.insert_one("ai_traces", _valid_envelope())

    assert (
        await client.get_database("platform").get_collection(collection_name).find_one() is not None
    )


@pytest.mark.asyncio
async def test_insert_one_allows_declared_metadata_fields_alongside_the_envelope(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    collection_name = "platform_ai_traces_metadata_probe"
    await client.get_database("platform").get_collection(collection_name).delete_many({})
    store = SystemStore(
        client,
        {"ai_traces": _Structure(physical_name=collection_name, encrypted=True)},
        database="platform",
    )

    document = {**_valid_envelope(), "trace_id": "trace-123"}

    await store.insert_one("ai_traces", document, allowed_metadata_fields=frozenset({"trace_id"}))

    stored = (
        await client.get_database("platform")
        .get_collection(collection_name)
        .find_one({"trace_id": "trace-123"})
    )
    assert stored is not None
