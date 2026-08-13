"""Published schema releases, and which one the runtime loads.

The platform's schema was a YAML file read at startup, so an approved analyzer
draft changed nothing until a deploy. These cover the store that closes that,
and the two properties it has to hold to be trustworthy: a release is never
rewritten, and "active" is a decision someone made rather than "the newest one
we happen to have".

Against real MongoDB, because both properties are enforced by a unique index
and a pointer document, and an in-memory double would prove neither.
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
from return_platform.dynamic_knowledge.config_loader import (
    load_active_schema,
    resolve_active_schema,
)
from return_platform.dynamic_knowledge.release_store import (
    ReleaseAlreadyPublished,
    SchemaReleaseStore,
)
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def store() -> Any:
    database = f"schema_release_{uuid.uuid4().hex[:12]}"
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
    """The real shipped schema under a new id. Content is not what is under test."""
    return baseline.model_copy(update={"configuration_release_id": release_id})


async def test_a_published_release_reads_back_as_the_same_schema(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, release_id), published_by="analyst-1")

    stored = await store.read(release_id)

    assert stored is not None
    # Every entity, field and projection, not just the identity: the release is
    # what the agent reasons over, and a lossy round trip would show up as
    # missing capability rather than as a storage bug.
    assert stored.entities == baseline.entities
    assert stored.graph.relationships == baseline.graph.relationships


async def test_a_release_cannot_be_republished(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """Immutable (D8). The checksum recorded at approval is worthless otherwise."""
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, release_id), published_by="analyst-1")

    with pytest.raises(ReleaseAlreadyPublished):
        await store.publish(_release(baseline, release_id), published_by="analyst-2")


async def test_publishing_does_not_activate(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """Recorded and live are different decisions.

    A store that activated on publish would make every cut release immediately
    the one the platform reasons over, with no chance to review it.
    """
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, release_id), published_by="analyst-1")

    active = await store.active()

    assert active is None or active.configuration_release_id != release_id


async def test_activation_points_at_the_named_release(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    first = f"release_{uuid.uuid4().hex[:8]}"
    second = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, first), published_by="analyst-1")
    await store.publish(_release(baseline, second), published_by="analyst-1")

    await store.activate(first)
    active = await store.active()

    assert active is not None
    # The one that was activated, not the newest. "Latest wins" would make
    # publishing a staged change silently deploy it.
    assert active.configuration_release_id == first


async def test_an_unpublished_release_cannot_be_activated(store: SchemaReleaseStore) -> None:
    """The alternative is a pointer at nothing and a runtime quietly on the file."""
    with pytest.raises(LookupError):
        await store.activate("release_that_never_existed")


async def test_the_runtime_prefers_the_activated_release_over_the_file(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, release_id), published_by="analyst-1")
    await store.activate(release_id)

    resolved = await resolve_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH, store)

    assert resolved.configuration_release_id == release_id
    assert resolved.configuration_release_id != baseline.configuration_release_id


async def test_the_runtime_falls_back_to_the_file_when_nothing_is_published(
    baseline: ActiveSchema,
) -> None:
    """Every installation starts here, and it has to boot.

    Refusing without a published release would make the analyzer a
    prerequisite for running the platform rather than a way to change it.
    """
    database = f"schema_release_empty_{uuid.uuid4().hex[:8]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    empty = SchemaReleaseStore(client, database)
    try:
        resolved = await resolve_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH, empty)
    finally:
        await client.drop_database(database)
        await client.close()

    assert resolved.configuration_release_id == baseline.configuration_release_id


async def test_an_unreachable_store_degrades_to_the_file(baseline: ActiveSchema) -> None:
    """A Mongo blip must not stop an associate finding an order.

    The file is a real schema that was running until the last publish, so
    degraded means behind, not broken.
    """

    class BrokenStore:
        async def active(self) -> ActiveSchema | None:
            raise RuntimeError("mongo is unreachable")

    resolved = await resolve_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH, BrokenStore())

    assert resolved.configuration_release_id == baseline.configuration_release_id


async def test_a_pointer_at_a_missing_release_does_not_silently_pick_another(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """Refusing to guess is what keeps "active" meaningful.

    Falling back to the newest published release here would run a schema
    nobody activated, which is exactly the failure the pointer exists to stop.
    """
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await store.publish(_release(baseline, release_id), published_by="analyst-1")
    await store.activate(release_id)
    await store._releases.delete_one({"configurationReleaseId": release_id})  # noqa: SLF001

    assert await store.active() is None
    # And the runtime keeps running, on the file.
    resolved = await resolve_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH, store)
    assert resolved.configuration_release_id == baseline.configuration_release_id


async def test_the_list_omits_the_release_bodies(
    store: SchemaReleaseStore, baseline: ActiveSchema
) -> None:
    """A console listing releases must not pull every schema to render rows."""
    await store.publish(
        _release(baseline, f"release_{uuid.uuid4().hex[:8]}"), published_by="analyst-1"
    )

    rows = await store.list_published()

    assert rows
    assert all("release" not in row for row in rows)
    assert all("configurationReleaseId" in row for row in rows)
