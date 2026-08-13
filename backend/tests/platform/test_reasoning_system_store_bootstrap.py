"""Real-Mongo proof that the production system-store manifest
(`config/platform/system_store.yaml`) actually bootstraps and that a
Settings-sourced encryption key round-trips through AesGcmEnvelopeEncryptor --
the two pieces `main.py`'s `_bootstrap_reasoning_system_store` wires together
at app startup (Phase 7 / Wave C2, Commit 2, step 0). Nothing in `src` did
this before this change; only test fixtures constructed a SystemStore/
EnvelopeEncryptor directly.
"""

from __future__ import annotations

import base64
import os
import uuid
from urllib.parse import quote

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import (
    DEFAULT_SYSTEM_STORE_MANIFEST_PATH,
    DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64,
    Settings,
)
from return_platform.platform.secrets.envelope import AesGcmEnvelopeEncryptor
from return_platform.platform.system_store.bootstrap import SystemStoreBootstrapper
from return_platform.platform.system_store.contracts import compute_manifest_fingerprint
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


def test_real_manifest_loads_and_includes_the_reasoning_structures() -> None:
    config = load_system_store_config(DEFAULT_SYSTEM_STORE_MANIFEST_PATH)
    structures = structure_definitions(config)
    logical_names = {definition.logical_name for definition in structures}
    assert {
        "reasoning_checkpoints",
        "reasoning_checkpoint_writes",
        "reasoning_runs",
        "order_discovery_query_evidence",
    } <= logical_names
    evidence = next(d for d in structures if d.logical_name == "order_discovery_query_evidence")
    assert evidence.encrypted is True
    assert evidence.physical_name == "platform_order_discovery_query_evidence"


def test_reasoning_encryption_key_default_is_a_valid_32_byte_key() -> None:
    settings = Settings.model_construct(
        reasoning_encryption_key=Settings.model_fields["reasoning_encryption_key"].default
    )
    decoded = base64.b64decode(settings.reasoning_encryption_key.get_secret_value())
    assert len(decoded) == 32
    encryptor = AesGcmEnvelopeEncryptor(key=decoded, key_ref="test-key")
    payload = encryptor.encrypt(b"round-trip-me")
    assert encryptor.decrypt(payload) == b"round-trip-me"


def _required_env(name: str) -> str:
    """Mirrors tests/conftest.py's _required_environment_variable -- avoids the
    shared test_settings fixture (which also requires NVIDIA_API_KEY/GOOGLE_API_KEY
    for AI-gateway fields this test never exercises)."""

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


@pytest.mark.asyncio
async def test_real_manifest_bootstraps_against_real_mongo_and_reuses_on_second_run() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    config = load_system_store_config(DEFAULT_SYSTEM_STORE_MANIFEST_PATH)
    suffix = uuid.uuid4().hex[:12]
    # Isolate this run's physical collections from the real production names so a
    # concurrent test/dev process bootstrapping the real manifest is unaffected.
    structures = tuple(
        definition.model_copy(update={"physical_name": f"{definition.physical_name}_test_{suffix}"})
        for definition in structure_definitions(config)
    )

    def _bootstrapper(owner: str) -> SystemStoreBootstrapper:
        return SystemStoreBootstrapper(
            lease_store=MongoLeaseStore(client, database="platform"),
            adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
            migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
            bootstrap_state=MongoBootstrapStateStore(client, database="platform"),
            guard=FencedMongoTransactionGuard(client, database="platform"),
            owner_instance_id=owner,
            fail_closed_on_drift=config.fail_closed_on_drift,
        )

    db = client.get_database("platform")
    try:
        first = await _bootstrapper("owner-1").bootstrap(
            list(structures), auto_bootstrap_missing=config.auto_bootstrap_missing_structures
        )
        assert set(first.created_structures) == {d.logical_name for d in structures}

        second = await _bootstrapper("owner-2").bootstrap(
            list(structures), auto_bootstrap_missing=config.auto_bootstrap_missing_structures
        )
        assert second.created_structures == ()
        assert set(second.existing_structures) == {d.logical_name for d in structures}

        system_store = SystemStore(
            client,
            {definition.logical_name: definition for definition in structures},
            database="platform",
        )
        encryptor = AesGcmEnvelopeEncryptor(
            key=base64.b64decode(DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64),
            key_ref="test-key",
        )
        raw = b"evidence-payload"
        payload = encryptor.encrypt(raw)
        await system_store.insert_one(
            "order_discovery_query_evidence",
            {
                "_id": "probe-1",
                "query_execution_id": "probe-1",
                "_envelope": {
                    "ciphertext": payload.ciphertext,
                    "key_ref": payload.key_ref,
                    "algorithm": payload.algorithm,
                    "version": payload.version,
                },
            },
            allowed_metadata_fields=frozenset({"query_execution_id"}),
        )
        stored = await system_store.read_only("order_discovery_query_evidence").find_one(
            {"_id": "probe-1"}
        )
        assert stored is not None
        envelope = stored["_envelope"]
        assert (
            encryptor.decrypt(
                type(payload)(
                    ciphertext=envelope["ciphertext"],
                    key_ref=envelope["key_ref"],
                    algorithm=envelope["algorithm"],
                    version=envelope["version"],
                )
            )
            == raw
        )
    finally:
        # Deliberately does NOT touch platform_schema_versions: it is keyed by
        # logical_name alone (shared with any real production bootstrap of this
        # same manifest on this Mongo instance), and Slice 3R.4's physical-identity
        # binding already makes a foreign/stale entry there safe to leave in place
        # (a physical_name mismatch is treated as version 0, never as "already
        # migrated") -- deleting it here would risk clobbering real state.
        for definition in structures:
            await db.get_collection(definition.physical_name).drop()
        await db.get_collection("platform_bootstrap_locks").delete_many(
            {"_id": "system_store_bootstrap"}
        )
        await db.get_collection("platform_bootstrap_state").delete_many(
            {"manifest_fingerprint": compute_manifest_fingerprint(list(structures))}
        )
        await client.close()
