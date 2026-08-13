"""Migration plans, stored against the pair of releases they describe.

Activation used to be a pointer flip in the dark. These cover the part of
closing that which only real MongoDB can prove: that a plan is written before
the pointer moves, that re-recording the same pair does not collide with the
unique index, and that the pointer itself carries what the migration was
understood to be.

Against real MongoDB, because the uniqueness is an index and the ordering is two
writes -- an in-memory double would demonstrate neither.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_migration import MigrationStrategy
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema

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


@pytest_asyncio.fixture(loop_scope="module")
async def store() -> Any:
    """A fresh database per test. The active pointer is global to a store, and
    a shared one would make each test depend on which ran first."""
    database = f"schema_migration_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    made = SchemaReleaseStore(client, database)
    await made.ensure_indexes()
    try:
        yield made
    finally:
        await client.drop_database(database)
        await client.close()


@pytest.fixture(scope="module")
def baseline() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _release(baseline: ActiveSchema, release_id: str) -> ActiveSchema:
    return baseline.model_copy(update={"configuration_release_id": release_id})


async def _publish(store: SchemaReleaseStore, baseline: ActiveSchema, release_id: str) -> str:
    await store.publish(_release(baseline, release_id), published_by="analyst-1")
    return release_id


async def test_activating_records_the_plan_it_activated_under(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """What a migration was understood to be has to outlive the decision."""
    first = await _publish(store, baseline, "release_first")

    plan = await store.activate(first)

    recorded = await store.recorded_plan(first, from_release_id=None)
    assert recorded == plan
    assert plan.strategy is MigrationStrategy.FULL_REBUILD


async def test_the_plan_is_keyed_on_the_pair_not_the_target(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """A plan is a statement about two releases. Keyed on the target alone, the
    second activation would silently overwrite the first one's answer.
    """
    first = await _publish(store, baseline, "release_first")
    second = await _publish(store, baseline, "release_second")
    await store.activate(first)
    await store.activate(second)
    await store.activate(first)

    from_nothing = await store.recorded_plan(first, from_release_id=None)
    from_second = await store.recorded_plan(first, from_release_id=second)

    assert from_nothing is not None
    assert from_second is not None
    assert from_nothing.strategy is MigrationStrategy.FULL_REBUILD
    assert from_second.strategy is MigrationStrategy.NO_CHANGE


async def test_re_recording_the_same_pair_does_not_collide(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """A plan is a pure function of two immutable releases, so a retried
    activation can only ever write the same answer -- and must not fail."""
    first = await _publish(store, baseline, "release_first")
    await store.activate(first)

    await store.activate(first)

    assert await store.recorded_plan(first, from_release_id=None) is not None


async def test_the_pointer_carries_what_the_migration_committed_to(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """ "What did activating the thing that is running commit us to" has to be
    answerable from the one document that says what is running."""
    first = await _publish(store, baseline, "release_first")
    second = await _publish(store, baseline, "release_second")
    await store.activate(first)
    await store.activate(second)

    pointer = await store._pointer.find_one({"_id": "active"})  # noqa: SLF001

    assert pointer is not None
    assert pointer["configurationReleaseId"] == second
    assert pointer["migratedFromReleaseId"] == first
    assert pointer["migrationStrategy"] == MigrationStrategy.NO_CHANGE.value


async def test_a_preview_leaves_no_trace(store: SchemaReleaseStore, baseline: ActiveSchema) -> None:
    """Reviewing a migration needs read rights and must not half-perform it."""
    first = await _publish(store, baseline, "release_first")

    plan = await store.preview_activation(first)

    assert plan.strategy is MigrationStrategy.FULL_REBUILD
    assert await store.recorded_plan(first, from_release_id=None) is None
    assert await store.active() is None


async def test_an_unpublished_release_can_neither_be_previewed_nor_activated(
    store: SchemaReleaseStore,
) -> None:
    with pytest.raises(LookupError):
        await store.preview_activation("release_that_never_existed")
    with pytest.raises(LookupError):
        await store.activate("release_that_never_existed")


async def test_a_rekeyed_release_is_planned_as_a_rebuild(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """The plan a real release round trip has to survive. A merge on a new key
    matches nothing and inserts a second node beside every existing one."""
    entity_id = next(iter(baseline.graph.nodes.values())).entity_id
    node = baseline.entity_node(entity_id)
    entity = baseline.entities[entity_id]
    other = next(name for name in entity.fields if name not in node.key_fields)
    rekeyed = baseline.model_copy(
        update={
            "graph": baseline.graph.model_copy(
                update={
                    "nodes": {
                        **baseline.graph.nodes,
                        node.projection_id: node.model_copy(
                            update={
                                "key_fields": (other,),
                                "property_fields": tuple(
                                    name for name in entity.fields if name != other
                                ),
                            }
                        ),
                    }
                }
            )
        }
    )
    first = await _publish(store, baseline, "release_first")
    await store.publish(
        rekeyed.model_copy(update={"configuration_release_id": "release_rekeyed"}),
        published_by="analyst-1",
    )
    await store.activate(first)

    plan = await store.activate("release_rekeyed")

    assert plan.requires_rebuild
    assert any("identity changes" in reason for reason in plan.rebuild_reasons)
    # And it survived Mongo intact, reasons and all.
    stored = await store.recorded_plan("release_rekeyed", from_release_id=first)
    assert stored == plan
