"""Real-Mongo proof of the analyzer's own persistence.

Everything else in `tests/graph_schema_analyzer/` runs against an in-memory
`PersistencePort` double, which cannot prove the two things that only the
database enforces:

* `source_samples` is declared `encrypted: true`, so the **store layer itself**
  must refuse a plaintext write there. A double will happily accept one.
* `schema_revisions` is unique on `(draft_id, sequence)`, which is what makes
  revision history genuinely append-only under concurrency. A double's
  `if any(...)` check is a different mechanism with different race behaviour.

Bootstraps a uniquely-suffixed copy of the real manifest so a concurrent process
using the production collection names is unaffected. Deliberately avoids the
shared `test_settings` fixture, which also demands NVIDIA_API_KEY/GOOGLE_API_KEY
that nothing here exercises.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import (
    DEFAULT_SYSTEM_STORE_MANIFEST_PATH,
    DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64,
)
from return_platform.graph_schema_analyzer.domain.mutation import AddEntity
from return_platform.graph_schema_analyzer.domain.schema_draft import (
    DraftStatus,
    GraphSchemaDraft,
    GraphSchemaShape,
)
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaRevision
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.persistence import SystemStorePersistence
from return_platform.graph_schema_analyzer.persistence.sample_repository import (
    SOURCE_SAMPLES,
    SourceSampleRepository,
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
from return_platform.platform.system_store.repository import (
    EncryptedStructureRequiresGuardedAccess,
    SystemStore,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin"


# Module-scoped: bootstrapping the full manifest costs ~20s, and doing it per
# test made this file a 3-minute tax on every suite run. Safe to share because
# every test below allocates its own uuid-based ids, so no two touch the same
# document -- if that ever stops being true, this must go back to function scope.
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
        owner_instance_id=f"analyzer-test-{suffix}",
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


# --- source_samples: encrypted at rest, enforced by the store ---------------


@pytest.mark.asyncio(loop_scope="module")
async def test_samples_round_trip_through_the_encrypted_structure(
    system_store: SystemStore,
) -> None:
    repository = SourceSampleRepository(system_store, _encryptor())
    rows = {"mongo_main.orders": [{"order_id": "ORD-1"}, {"order_id": "ORD-2"}]}

    await repository.save(
        samples_ref="samples-1", rows_by_dataset=rows, expires_at=NOW + timedelta(days=1)
    )
    assert await repository.load("samples-1") == rows


@pytest.mark.asyncio(loop_scope="module")
async def test_sample_content_is_not_readable_without_the_key(
    system_store: SystemStore,
) -> None:
    """The point of the encrypted structure: the raw document must not contain
    the business values, only ciphertext."""
    repository = SourceSampleRepository(system_store, _encryptor())
    await repository.save(
        samples_ref="samples-2",
        rows_by_dataset={"mongo_main.orders": [{"customer_email": "person@example.com"}]},
        expires_at=NOW + timedelta(days=1),
    )
    stored = await system_store.read_only(SOURCE_SAMPLES).find_one({"samples_ref": "samples-2"})
    assert stored is not None
    assert "person@example.com" not in str(stored)
    assert set(stored["_envelope"]) == {"ciphertext", "key_ref", "algorithm", "version"}


@pytest.mark.asyncio(loop_scope="module")
async def test_the_store_itself_refuses_a_plaintext_write_to_source_samples(
    system_store: SystemStore,
) -> None:
    """Not the repository's discipline -- the store's. This is what makes
    "samples are never persisted unclassified" a property rather than a habit."""
    with pytest.raises(Exception) as caught:
        await system_store.insert_one(
            SOURCE_SAMPLES,
            {"samples_ref": "plain-1", "rows": [{"order_id": "ORD-1"}]},
            allowed_metadata_fields=frozenset({"samples_ref"}),
        )
    assert "envelope" in str(caught.value).lower() or "encrypt" in str(caught.value).lower()


@pytest.mark.asyncio(loop_scope="module")
async def test_an_encrypted_structure_hands_out_no_raw_collection(
    system_store: SystemStore,
) -> None:
    with pytest.raises(EncryptedStructureRequiresGuardedAccess):
        system_store.collection(SOURCE_SAMPLES)


@pytest.mark.asyncio(loop_scope="module")
async def test_a_missing_samples_reference_reads_as_absent_not_an_error(
    system_store: SystemStore,
) -> None:
    """Expiry is normal: a snapshot's metadata outlives its samples by design."""
    repository = SourceSampleRepository(system_store, _encryptor())
    assert await repository.load("never-written") is None


# --- revision history is append-only, enforced by a unique index ------------


def _draft() -> GraphSchemaDraft:
    return GraphSchemaDraft(
        draft_id=f"draft-{uuid.uuid4()}",
        analysis_id="a1",
        shape=GraphSchemaShape(),
        created_at=NOW,
        updated_at=NOW,
    )


def _revision(draft_id: str, sequence: int) -> SchemaRevision:
    return SchemaRevision(
        revision_id=f"revision-{uuid.uuid4()}",
        draft_id=draft_id,
        sequence=sequence,
        mutations=(AddEntity(label="Order", source_dataset="orders"),),
        author="analyst",
        created_at=NOW,
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_two_revisions_cannot_share_a_sequence_number(
    system_store: SystemStore,
) -> None:
    """The database enforces this, not the writer. Without it a concurrent
    second writer could produce two "revision 4" and the history would stop
    being a trustworthy record of what was decided."""
    persistence = SystemStorePersistence(system_store)
    draft = _draft()
    await persistence.create_draft(draft)

    await persistence.append_revision(_revision(draft.draft_id, 1))
    with pytest.raises(DuplicateKeyError):
        await persistence.append_revision(_revision(draft.draft_id, 1))


@pytest.mark.asyncio(loop_scope="module")
async def test_revisions_are_returned_in_sequence_order(system_store: SystemStore) -> None:
    persistence = SystemStorePersistence(system_store)
    draft = _draft()
    await persistence.create_draft(draft)
    for sequence in (3, 1, 2):
        await persistence.append_revision(_revision(draft.draft_id, sequence))

    listed = await persistence.list_revisions(draft.draft_id)
    assert [revision.sequence for revision in listed] == [1, 2, 3]


@pytest.mark.asyncio(loop_scope="module")
async def test_a_stale_draft_write_is_rejected_by_compare_and_set(
    system_store: SystemStore,
) -> None:
    """Two analysts editing one schema must not silently clobber each other."""
    from return_platform.graph_schema_analyzer.domain.errors import ConcurrentModification

    persistence = SystemStorePersistence(system_store)
    draft = _draft()
    await persistence.create_draft(draft)

    first = draft.mutated(GraphSchemaShape(), occurred_at=NOW)
    await persistence.save_draft(first, expected_version=draft.version)

    # A second writer still holding the original read.
    stale = draft.mutated(GraphSchemaShape(), occurred_at=NOW)
    with pytest.raises(ConcurrentModification):
        await persistence.save_draft(stale, expected_version=draft.version)

    reloaded = await persistence.load_draft(draft.draft_id)
    assert reloaded.current_revision == 1
    assert reloaded.status is DraftStatus.DRAFT


@pytest.mark.asyncio(loop_scope="module")
async def test_a_snapshot_round_trips_with_its_content_hash_intact(
    system_store: SystemStore,
) -> None:
    """Loading re-derives the hash, so a snapshot that survives the round trip
    is one whose metadata was not altered in storage."""
    persistence = SystemStorePersistence(system_store)
    snapshot = SourceSchemaSnapshot.create(
        snapshot_id=f"snap-{uuid.uuid4()}",
        analysis_id="a1",
        datasets=(
            DatasetMetadata(
                source_id="mongo_main",
                dataset_name="orders",
                fields=(FieldMetadata(field_name="order_id", declared_type="string"),),
            ),
        ),
        sample_classification=SampleClassification.NONE,
        captured_at=NOW,
    )
    await persistence.save_snapshot(snapshot)
    loaded = await persistence.load_snapshot(snapshot.snapshot_id)
    assert loaded.content_hash == snapshot.content_hash
    assert loaded.describes_same_shape_as(snapshot)
