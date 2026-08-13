"""W2.2's Validation clause, against real Mongo: `salesInv` -> `salesInvV2`.

The clause is that renaming a physical source collection is achievable through
configuration alone, with no code change, and that the platform then reads the
renamed collection. Asserting that on fakes would prove nothing -- the failure
being ruled out is a read that lands on a collection nobody told it to use, and
a fake collection answers to whatever name it is asked for. So these run the
real `OperationalRepository` against a real Mongo and then look at what is
physically in each database.

Both configuration routes are covered, because both are "configuration alone"
and they resolve differently:

* editing the active schema, which changes `object_ref.name` on the source asset
  and is what an installation does when the upstream system renames a
  collection permanently; and
* storing a rebinding, which leaves the file alone and is what an operator does
  at 3am when a collection has been restored somewhere else.

`directConnection=true` on the DSN deliberately -- the deployment runs a
single-node replica set that advertises its container hostname, so topology
discovery from the host resolves a name that does not exist and every operation
times out.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
import yaml
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import (
    DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH,
    Settings,
)
from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.schema import SourceAssetDefinition
from return_platform.dynamic_knowledge.source_binding_store import SourceBindingStore
from return_platform.dynamic_knowledge.source_bindings import SourceBinding
from return_platform.operations.repository import (
    OperationalRepository,
    SourceDatasetUnresolvedError,
)
from return_platform.operations.seed_manifest import SOURCE_SALES_DATASET

pytestmark = pytest.mark.asyncio

#: What the shipped schema calls the collection today, and what W2.2 says an
#: installation must be able to rename without touching Python.
SHIPPED_COLLECTION = "salesInv"
RENAMED_COLLECTION = "salesInvV2"
#: `MINIMUM_SEED_RECORD_LIMIT`, which is the smallest apply the manifest allows
#: and is plenty here -- these tests distinguish "everything" from "nothing".
SEED_RECORDS = 10


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/admin?authSource=admin&directConnection=true"
    )


@pytest.fixture(scope="module")
def shipped_schema_document() -> dict[str, Any]:
    raw = yaml.safe_load(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert raw["sources"][SOURCE_SALES_DATASET]["object_ref"]["name"] == SHIPPED_COLLECTION
    return raw


def _schema_file(document: dict[str, Any], destination: Path) -> Path:
    """Write a schema whose checksum matches its content.

    Recomputed rather than carried over, because `load_active_schema` verifies
    it and a copy with a stale checksum would fail as a configuration-integrity
    error -- which is the right behaviour and would tell us nothing about
    binding resolution.
    """
    payload = {key: value for key, value in document.items() if key != "configuration_checksum"}
    payload["configuration_checksum"] = sha256_digest(payload)
    destination.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return destination


def _renamed(document: dict[str, Any], collection: str) -> dict[str, Any]:
    """The one edit a collection rename is: `object_ref.name` on one asset.

    The asset id, every entity that reads through it and every field path are
    untouched -- which is the shape/binding split working. If a rename needed
    any of the rest to move, the split would not exist.
    """
    renamed = {key: dict(value) if key == "sources" else value for key, value in document.items()}
    sources = dict(renamed["sources"])
    sales = dict(sources[SOURCE_SALES_DATASET])
    sales["object_ref"] = {**sales["object_ref"], "name": collection}
    sources[SOURCE_SALES_DATASET] = sales
    renamed["sources"] = sources
    return renamed


def _renamed_asset_id(document: dict[str, Any], asset_id: str) -> dict[str, Any]:
    """Call the sales asset something else, everywhere it is named.

    Both the `sources` key and its `source_asset_id`, plus every entity that
    reads through it -- anything less does not validate.
    """
    sources = {
        (asset_id if name == SOURCE_SALES_DATASET else name): (
            {**asset, "source_asset_id": asset_id} if name == SOURCE_SALES_DATASET else asset
        )
        for name, asset in document["sources"].items()
    }
    entities = {
        entity_id: (
            {**entity, "source_asset_id": asset_id}
            if entity.get("source_asset_id") == SOURCE_SALES_DATASET
            else entity
        )
        for entity_id, entity in document["entities"].items()
    }
    return {**document, "sources": sources, "entities": entities}


class _Fixture:
    """One isolated platform database, one isolated source database."""

    def __init__(self, base: Settings, schema_path: Path) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.platform_database = f"w22_platform_{suffix}"
        self.source_database = f"w22_source_{suffix}"
        self.client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
        self.settings = base.model_copy(
            update={
                "mongo_database": self.platform_database,
                "source_mongo_database": self.source_database,
                "dynamic_knowledge_schema_path": schema_path,
                "seed_version": f"w22-{suffix}",
            }
        )

    @property
    def repository(self) -> OperationalRepository:
        """A new one each time, because the catalogue is cached per instance.

        A rebinding written after a repository was built is meant to reach the
        next request, not the one in flight, so a test that changed a binding
        and reused the same object would be asserting the opposite of the
        documented behaviour.
        """
        return OperationalRepository(self.client, self.settings, self.client)

    def source(self, collection: str) -> Any:
        return self.client[self.source_database][collection]

    async def apply_seed(self) -> Any:
        async def progress(count: int, message: str) -> None:
            del count, message

        return await self.repository.apply_seed(
            actor_id="w22-test",
            record_limit=SEED_RECORDS,
            cancel_check=lambda: None,
            progress=progress,
        )

    async def aclose(self) -> None:
        await self.client.drop_database(self.platform_database)
        await self.client.drop_database(self.source_database)
        await self.client.close()


@pytest_asyncio.fixture
async def shipped(
    test_settings: Settings, shipped_schema_document: dict[str, Any], tmp_path: Path
) -> AsyncIterator[_Fixture]:
    path = _schema_file(shipped_schema_document, tmp_path / "shipped.yaml")
    fixture = _Fixture(test_settings, path)
    try:
        yield fixture
    finally:
        await fixture.aclose()


@pytest_asyncio.fixture
async def renamed(
    test_settings: Settings, shipped_schema_document: dict[str, Any], tmp_path: Path
) -> AsyncIterator[_Fixture]:
    path = _schema_file(
        _renamed(shipped_schema_document, RENAMED_COLLECTION), tmp_path / "renamed.yaml"
    )
    fixture = _Fixture(test_settings, path)
    try:
        yield fixture
    finally:
        await fixture.aclose()


async def test_the_shipped_configuration_still_lands_on_sales_inv(shipped: _Fixture) -> None:
    """The control.

    Without it a rename test also passes for a platform that reads nothing at
    all, which is the failure it is most likely to be confused with.
    """
    status = await shipped.apply_seed()

    assert status.ready is True
    assert await shipped.source(SHIPPED_COLLECTION).count_documents({}) == SEED_RECORDS
    assert await shipped.source(RENAMED_COLLECTION).count_documents({}) == 0


async def test_renaming_the_collection_in_configuration_moves_the_seed(
    renamed: _Fixture,
) -> None:
    """The write half of the clause: no code was edited, only `object_ref.name`."""
    status = await renamed.apply_seed()

    assert status.ready is True
    assert await renamed.source(RENAMED_COLLECTION).count_documents({}) == SEED_RECORDS
    # Not "fewer than before": nothing at all may be left behind in the
    # collection the rename retired, or the platform is writing to both.
    assert await renamed.source(SHIPPED_COLLECTION).count_documents({}) == 0


async def test_renaming_the_collection_in_configuration_moves_the_indexes(
    renamed: _Fixture,
) -> None:
    """An index left on the retired collection is worse than a missing one.

    The query it was built for still collection-scans, while an operator
    inspecting the database can see that an index exists and conclude the
    lookup is covered.
    """
    await renamed.apply_seed()

    renamed_indexes = await renamed.source(RENAMED_COLLECTION).index_information()
    shipped_indexes = await renamed.source(SHIPPED_COLLECTION).index_information()

    assert "sales_order_number_unique" in renamed_indexes
    assert "sales_customer_lookup" in renamed_indexes
    assert "sales_order_number_unique" not in shipped_indexes


async def test_the_platform_reads_the_renamed_collection(renamed: _Fixture) -> None:
    """The read half, through the production entry point.

    `source_order` is what order discovery calls, and it has a documented
    fallback to the legacy `orders` collection when the sales dataset misses --
    so a read that resolved to the retired (empty) collection would still return
    something. Asserting on `sourceAssetId` rather than on "not None" is what
    makes this distinguish the two.
    """
    await renamed.apply_seed()
    seeded = await renamed.source(RENAMED_COLLECTION).find_one({})
    assert seeded is not None
    order_reference = str(seeded["salesHdrEventData"]["orderId"])

    order = await renamed.repository.source_order(order_reference)

    assert order is not None
    assert order["sourceAssetId"] == "SOURCE_MONGODB_SALES_INV"
    assert order["orderReference"] == order_reference
    assert order["items"]


async def test_a_stored_rebinding_moves_the_read_without_editing_the_schema(
    shipped: _Fixture,
) -> None:
    """The other configuration route: the file is the shipped one, untouched.

    This is the route that exists for infrastructure that moved without anyone
    approving a schema change -- a collection restored under another name after
    an incident. It has to work without a publish, or the only remedy at 3am is
    a deploy.
    """
    restored = "salesInvRestored"
    await shipped.apply_seed()
    documents = await shipped.source(SHIPPED_COLLECTION).find({}).to_list()
    await shipped.source(restored).insert_many(documents)
    await shipped.source(SHIPPED_COLLECTION).delete_many({})

    await SourceBindingStore(shipped.client, shipped.platform_database).rebind(
        SourceBinding(
            dataset=SOURCE_SALES_DATASET,
            asset=SourceAssetDefinition(
                source_asset_id=SOURCE_SALES_DATASET,
                connector_type="MONGODB",
                connection_ref="vault://data-sources/source-mongodb",
                object_ref={"database": shipped.source_database, "name": restored},
                incremental_cursor_field="source_updated_at",
            ),
        ),
        changed_by="operator-w22",
    )

    seeded = await shipped.source(restored).find_one({})
    assert seeded is not None
    order_reference = str(seeded["salesHdrEventData"]["orderId"])
    order = await shipped.repository.source_order(order_reference)

    assert order is not None
    assert order["orderReference"] == order_reference
    assert order["sourceAssetId"] == "SOURCE_MONGODB_SALES_INV"


async def test_readiness_reports_an_unplaceable_dataset_instead_of_failing(
    test_settings: Settings, shipped_schema_document: dict[str, Any], tmp_path: Path
) -> None:
    """The one place resolution must not raise.

    `seed_status` renders a diagnostics card, and a schema that cannot place the
    sales dataset is precisely when an operator needs that page to draw. Raising
    would take down the whole card list -- including the cards that would tell
    them which dependency is wrong.

    The schema here renames the *asset id* rather than deleting the asset,
    because `ActiveSchema` refuses to validate a schema whose entities reference
    a source that is not there -- `contact_point` reads through `source_sales`.
    So the only way this repository can fail to place a dataset is a release
    that calls its sources something else, which is also the realistic one.
    """
    renamed_asset = _renamed_asset_id(shipped_schema_document, "sales_inventory")
    fixture = _Fixture(test_settings, _schema_file(renamed_asset, tmp_path / "renamed_id.yaml"))
    try:
        status = await fixture.repository.seed_status()
    finally:
        await fixture.aclose()

    assert status.ready is False
    assert status.counts[SOURCE_SALES_DATASET] == 0
    assert any(SOURCE_SALES_DATASET in error for error in status.validationErrors)


async def test_a_dataset_configuration_does_not_name_is_refused(shipped: _Fixture) -> None:
    """Not defaulted to a plausible collection.

    The catalogue's own rule -- an unknown dataset resolves to nothing rather
    than to a neighbour -- has to survive being consumed here, because the
    alternative is a repository that reads stale documents from a collection a
    rename retired and reports success.
    """
    with pytest.raises(SourceDatasetUnresolvedError):
        await shipped.repository.source_dataset("no_such_dataset")
