"""Stored rebindings, and what a rebinding does to the next release.

The store holds only what someone deliberately changed. These cover that -- and
the property that matters most for trust: a rebinding does not touch a release
that is already published. Infrastructure moving must not silently re-point a
schema someone approved.
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
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema, SourceAssetDefinition
from return_platform.dynamic_knowledge.source_binding_store import SourceBindingStore
from return_platform.dynamic_knowledge.source_bindings import SourceBinding, catalogue_from

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
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context() -> Any:
    database = f"source_binding_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    bindings = SourceBindingStore(client, database)
    releases = SchemaReleaseStore(client, database)
    await bindings.ensure_indexes()
    await releases.ensure_indexes()
    try:
        yield bindings, releases
    finally:
        await client.drop_database(database)
        await client.close()


@pytest.fixture(scope="module")
def baseline() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _asset(source_asset_id: str) -> SourceAssetDefinition:
    return SourceAssetDefinition(
        source_asset_id=source_asset_id,
        connector_type="MONGODB",
        object_ref={"database": "restore_2026", "name": "salesInv"},
    )


async def test_a_rebinding_round_trips(context: Any) -> None:
    bindings, _ = context
    dataset = f"dataset_{uuid.uuid4().hex[:8]}"

    await bindings.rebind(
        SourceBinding(dataset=dataset, asset=_asset("restored")),
        changed_by="operator-1",
    )

    stored = {binding.dataset: binding for binding in await bindings.list()}
    assert stored[dataset].asset.source_asset_id == "restored"
    assert stored[dataset].asset.object_ref == {"database": "restore_2026", "name": "salesInv"}


async def test_rebinding_twice_replaces_rather_than_duplicates(context: Any) -> None:
    """Rebinding is an ordinary act, and the second one is not a mistake."""
    bindings, _ = context
    dataset = f"dataset_{uuid.uuid4().hex[:8]}"

    await bindings.rebind(
        SourceBinding(dataset=dataset, asset=_asset("first")),
        changed_by="operator-1",
    )
    await bindings.rebind(
        SourceBinding(dataset=dataset, asset=_asset("second")),
        changed_by="operator-2",
    )

    matching = [binding for binding in await bindings.list() if binding.dataset == dataset]
    assert len(matching) == 1
    assert matching[0].asset.source_asset_id == "second"


async def test_clearing_returns_a_dataset_to_configuration(
    context: Any, baseline: ActiveSchema
) -> None:
    bindings, _ = context
    dataset = next(iter(baseline.sources))
    await bindings.rebind(
        SourceBinding(dataset=dataset, asset=_asset("restored")),
        changed_by="operator-1",
    )

    assert await bindings.clear(dataset) is True

    catalogue = catalogue_from(baseline, await bindings.list())
    assert catalogue.resolve(dataset) == baseline.sources[dataset]


async def test_clearing_something_that_was_never_bound_is_not_an_error(context: Any) -> None:
    """ "Follow the file" is the same intention either way.

    A 404 would make the console's reset conditional on state the operator
    cannot see.
    """
    bindings, _ = context

    assert await bindings.clear(f"never_bound_{uuid.uuid4().hex[:8]}") is False


async def test_a_rebinding_does_not_repoint_a_published_release(
    context: Any, baseline: ActiveSchema
) -> None:
    """The property that makes releases worth approving.

    A release captured its sources when it was compiled. If a later
    infrastructure edit changed where it reads from, the thing running would no
    longer be the thing anyone reviewed.
    """
    bindings, releases = context
    dataset = next(iter(baseline.sources))
    release_id = f"release_{uuid.uuid4().hex[:8]}"
    await releases.publish(
        baseline.model_copy(update={"configuration_release_id": release_id}),
        published_by="analyst-1",
    )
    await releases.activate(release_id)

    await bindings.rebind(
        SourceBinding(dataset=dataset, asset=_asset("restored")),
        changed_by="operator-1",
    )

    active = await releases.active()
    assert active is not None
    assert active.sources[dataset] == baseline.sources[dataset]
    # The rebinding is real; it simply reaches the runtime at the next publish.
    assert (
        catalogue_from(baseline, await bindings.list()).resolve(dataset)
        != (baseline.sources[dataset])
    )
