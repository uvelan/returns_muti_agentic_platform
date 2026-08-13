"""Canonical index drift detection (Slice 3R.5): comparing only the index name is not
enough to prove an existing index matches what the manifest declares. `ensure_indexes`
must compare the full canonical shape (key order/direction, unique, partial filter),
and a same-name/different-definition index must be detected as drift and never
auto-repaired -- while a semantically equivalent index (Mongo's normalized defaults)
must be accepted as a match, not flagged as drift. Runs against a real MongoDB replica
set: index definitions are genuine server-reported metadata, not something a mock can
fake convincingly."""

from __future__ import annotations

import uuid

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.contracts import StructureDefinition
from return_platform.platform.system_store.mongo import (
    MongoSystemStoreAdapter,
    PymongoStructureGateway,
)

# Live infrastructure: this module opens a real MongoDB client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra


@pytest.mark.asyncio
async def test_same_name_different_definition_index_is_detected_as_drift(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    physical_name = f"probe_index_drift_{uuid.uuid4().hex[:8]}"
    await db.get_collection(physical_name).drop()
    await db.create_collection(physical_name)
    # Pre-existing index with the same name but NOT unique -- drift from what the
    # manifest below declares (unique=True).
    await db.get_collection(physical_name).create_index(
        [("conversation_id", 1)], name="conversation_id_unique", unique=False
    )

    adapter = MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform"))
    definition = StructureDefinition(
        logical_name="probe",
        physical_name=physical_name,
        schema_version=1,
        indexes=(
            {"name": "conversation_id_unique", "fields": ["conversation_id"], "unique": True},
        ),
    )

    result = await adapter.ensure_indexes(definition)

    assert result.created == ()
    assert len(result.drifted) == 1
    assert result.drifted[0].index_name == "conversation_id_unique"


@pytest.mark.asyncio
async def test_equivalent_index_is_accepted_not_flagged_as_drift(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    physical_name = f"probe_index_match_{uuid.uuid4().hex[:8]}"
    await db.get_collection(physical_name).drop()
    await db.create_collection(physical_name)

    adapter = MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform"))
    definition = StructureDefinition(
        logical_name="probe",
        physical_name=physical_name,
        schema_version=1,
        indexes=(
            {"name": "conversation_id_unique", "fields": ["conversation_id"], "unique": True},
        ),
    )

    first = await adapter.ensure_indexes(definition)
    assert first.created == ("conversation_id_unique",)
    assert first.drifted == ()

    # Re-running against the identical declared definition, with the index already
    # created exactly as declared, must be accepted silently -- not flagged as drift,
    # and not recreated.
    second = await adapter.ensure_indexes(definition)
    assert second.created == ()
    assert second.drifted == ()


@pytest.mark.asyncio
async def test_missing_index_is_created_when_absent(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    physical_name = f"probe_index_missing_{uuid.uuid4().hex[:8]}"
    await db.get_collection(physical_name).drop()
    await db.create_collection(physical_name)

    adapter = MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform"))
    definition = StructureDefinition(
        logical_name="probe",
        physical_name=physical_name,
        schema_version=1,
        indexes=({"name": "audit_ts", "fields": ["created_at"], "unique": False},),
    )

    result = await adapter.ensure_indexes(definition)

    assert result.created == ("audit_ts",)
    assert result.drifted == ()
