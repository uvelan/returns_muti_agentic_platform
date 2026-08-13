"""The MongoDB inspection adapter against a real MongoDB.

The interesting cases here are the ones a schemaless store creates and a
relational one cannot: a field that is absent from some documents, a field whose
values disagree on type, and a `list_relationships` that has nothing true to say.
Each of those is a place where a connector can be confidently wrong, and the
analyzer's validation treats a declared type as fact.

`directConnection=true` on the client: the single-node replica set advertises its
container hostname, so topology discovery from the host resolves a name that does
not exist and every operation times out. A direct connection makes this module run
identically on the host and inside the compose network.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.bootstrap.adapters.source_inspection_mongodb import (
    build_mongo_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.graph_schema_analyzer.application.source_inspection import (
    ScopedSourceInspection,
)
from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    ObjectKind,
    SourceInspectionPort,
)

SOURCE_ID = "return_source_mongo"
DATABASE = "return_platform"


def _client(settings: Settings) -> AsyncMongoClient[dict[str, Any]]:
    return AsyncMongoClient(settings.mongo_dsn.get_secret_value(), directConnection=True)


@pytest_asyncio.fixture
async def inspected_collection(test_settings: Settings) -> AsyncIterator[tuple[str, str]]:
    """A collection whose documents deliberately disagree with each other.

    `nickname` is missing from one document, `code` holds a string in two and an
    int in the third, and `retired_at` is null everywhere -- the three shapes a
    Mongo connector has to describe without inventing a schema.
    """
    client = _client(test_settings)
    name = f"insp_orders_{uuid.uuid4().hex[:10]}"
    empty = f"insp_empty_{uuid.uuid4().hex[:10]}"
    database = client[DATABASE]
    await database[name].insert_many(
        [
            {
                "_id": "doc-1",
                "order_id": "O-1",
                "code": "AA",
                "nickname": "first",
                "retired_at": None,
                "changed_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
            {
                "_id": "doc-2",
                "order_id": "O-2",
                "code": "BB",
                "retired_at": None,
                "changed_at": datetime(2026, 1, 2, tzinfo=UTC),
            },
            {
                "_id": "doc-3",
                "order_id": "O-3",
                "code": 7,
                "nickname": "third",
                "retired_at": None,
                "changed_at": datetime(2026, 1, 3, tzinfo=UTC),
            },
        ]
    )
    await database[empty].insert_one({"_id": "seed"})
    await database[empty].delete_many({})
    try:
        yield (name, empty)
    finally:
        await database[name].drop()
        await database[empty].drop()
        await client.close()


@pytest_asyncio.fixture
async def inspection(test_settings: Settings) -> AsyncIterator[SourceInspectionPort]:
    client = _client(test_settings)
    try:
        yield build_mongo_source_inspection_adapter(
            client, database_name=DATABASE, source_id=SOURCE_ID
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_reports_the_server_it_actually_reached(
    inspection: SourceInspectionPort,
) -> None:
    """Catches an adapter reporting success without opening a connection."""
    result = await inspection.validate(source_id=SOURCE_ID)
    assert result.reachable is True
    assert result.server_version


@pytest.mark.asyncio
async def test_list_objects_excludes_the_stores_own_bookkeeping_collections(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """Analysing `system.*` or the platform's own collections would propose a
    graph schema for our internals rather than for the business data."""
    objects = await inspection.list_objects(source_id=SOURCE_ID)
    names = {item.object_name for item in objects}
    assert inspected_collection[0] in names
    assert not any(name.startswith(("system.", "platform_")) for name in names)
    assert all(item.object_kind in (ObjectKind.COLLECTION, ObjectKind.VIEW) for item in objects)


@pytest.mark.asyncio
async def test_a_field_missing_from_some_documents_is_reported_nullable(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """In a schemaless store absence and null are the same fact to anyone
    deciding whether a graph property can be required. Reporting `nickname` as
    non-nullable would produce a constraint the data cannot satisfy."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=inspected_collection[0]
    )
    by_name = {field.field_name: field for field in description.fields}
    assert by_name["nickname"].nullable is True
    assert by_name["order_id"].nullable is False


@pytest.mark.asyncio
async def test_a_field_whose_values_disagree_on_type_is_reported_as_mixed(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """Picking one of two observed types would launder a real disagreement in the
    data into a confident mapping that fails at sync time -- the analyzer's
    TYPE_COMPATIBILITY check treats a declared type as fact."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=inspected_collection[0]
    )
    by_name = {field.field_name: field for field in description.fields}
    assert by_name["code"].declared_type == "mixed"
    assert by_name["order_id"].declared_type == "string"


@pytest.mark.asyncio
async def test_a_field_that_is_null_everywhere_is_reported_as_unknown_not_guessed(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """There is genuinely no type information in a column of nulls. A guess here
    is worse than the absence, because nothing downstream would know it was
    one."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=inspected_collection[0]
    )
    by_name = {field.field_name: field for field in description.fields}
    assert by_name["retired_at"].declared_type == "unknown"


@pytest.mark.asyncio
async def test_an_empty_collection_is_described_with_no_fields_rather_than_a_guess(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """An empty-but-confident schema is the worst answer available: it looks like
    a described collection and cannot be distinguished from one."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=inspected_collection[1]
    )
    assert description.fields == ()
    assert description.approximate_row_count == 0


@pytest.mark.asyncio
async def test_profile_reports_null_rate_and_identifier_candidates(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """The statistics W4.8 ranks elicitation questions on. `nickname` is absent
    from one of three documents, so a third is the honest rate."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=inspected_collection[0], sample_size=10
    )
    by_name = {field.field_name: field for field in profile.fields}
    assert profile.sampled_rows == 3
    assert by_name["nickname"].null_rate == pytest.approx(1 / 3)
    assert by_name["retired_at"].null_rate == 1.0
    assert by_name["order_id"].identifier_candidate is True
    assert by_name["changed_at"].change_tracking_candidate is True


@pytest.mark.asyncio
async def test_profile_returns_no_document_values(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """`profile` is the weaker grant. If a value could reach the result,
    profiling would be equivalent to sampling and the distinction decorative."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=inspected_collection[0], sample_size=10
    )
    serialised = profile.model_dump_json()
    assert "O-1" not in serialised
    assert "first" not in serialised


@pytest.mark.asyncio
async def test_list_indexes_reports_the_id_index_as_the_primary_one(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """`_id_` is the only index Mongo creates unasked and the only one guaranteed
    unique on every document, which is what "primary" means to the other three
    backends -- so the four connectors answer the same question the same way."""
    indexes = await inspection.list_indexes(
        source_id=SOURCE_ID, object_name=inspected_collection[0]
    )
    primary = [index for index in indexes if index.primary]
    assert len(primary) == 1
    assert primary[0].fields == ("_id",)


@pytest.mark.asyncio
async def test_mongodb_declares_no_relationships_rather_than_inferring_them(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """The honest answer, and the one this test exists to keep. An edge guessed
    from a field being named `customerId` would arrive in the same shape as SQL
    Server's `sys.foreign_keys` entries, and nothing downstream could tell the
    guess from the fact."""
    assert (
        await inspection.list_relationships(
            source_id=SOURCE_ID, object_name=inspected_collection[0]
        )
        == ()
    )


@pytest.mark.asyncio
async def test_an_ungranted_field_never_leaves_the_database(
    inspection: SourceInspectionPort, inspected_collection: tuple[str, str]
) -> None:
    """The projection has to be pushed into the `find`, or the ungranted field
    crosses the network and is only then discarded. Asserted through what comes
    back, since that is the observable the grant is about."""
    scoped = ScopedSourceInspection(
        inspection,
        scope=InspectionScope(
            sources=(
                SourceScope(
                    source_id=SOURCE_ID,
                    objects=(
                        ObjectScope(
                            object_name=inspected_collection[0],
                            fields=frozenset({"order_id"}),
                        ),
                    ),
                    max_sample_rows=3,
                ),
            )
        ),
    )
    rows = await scoped.sample(source_id=SOURCE_ID, object_name=inspected_collection[0], limit=3)
    assert rows
    assert all(set(row) <= {"order_id"} for row in rows)
    with pytest.raises(ScopeViolation, match="nickname"):
        await scoped.sample(
            source_id=SOURCE_ID,
            object_name=inspected_collection[0],
            limit=1,
            fields=("nickname",),
        )
