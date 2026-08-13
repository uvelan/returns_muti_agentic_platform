"""One `SourceInspectionPort` over two different stores, resolved per source.

The defect this guards is the one `SourceConnectorsByType`'s own docstring
describes: a connector type does not identify a store, so resolving by type alone
sends a source to whichever connector was registered for MONGODB or MSSQL and it
reads objects that are not there -- which surfaces as an empty result, not as a
misconfiguration. Two real stores are the only way to tell the two apart, because
a fake would answer whatever it was asked.

It also pins that W4.5 extended the existing dispatch rather than adding a second
registry (D1): if a parallel one appeared, this test would be constructing it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pymssql
import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.bootstrap.adapters.source_inspection_mongodb import (
    build_mongo_source_inspection_adapter,
)
from return_platform.bootstrap.adapters.source_inspection_routing import (
    build_routing_source_inspection_adapter,
)
from return_platform.bootstrap.adapters.source_inspection_sqlserver import (
    build_sqlserver_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.graph_schema_analyzer.ports.source_port import (
    ObjectKind,
    SourceInspectionPort,
)
from return_platform.source_connectors.registry import UnreachableSource
from return_platform.source_connectors.sqlserver import SqlServerConnectionSettings

MONGO_SOURCE = "routing_mongo_source"
SQL_SOURCE = "routing_sql_source"
DATABASE = "return_platform"


class _Fixture:
    def __init__(self, collection: str, table: str) -> None:
        self.collection = collection
        self.table = table

    @property
    def sql_object(self) -> str:
        return f"dbo.{self.table}"


@pytest_asyncio.fixture
async def two_stores(test_settings: Settings) -> AsyncIterator[_Fixture]:
    """One collection in Mongo and one table in SQL Server, named differently.

    Different names on purpose: identical names would let a misrouted call
    succeed against the wrong store and the test would pass anyway.
    """
    suffix = uuid.uuid4().hex[:10]
    fixture = _Fixture(f"routing_collection_{suffix}", f"routing_table_{suffix}")

    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value(), directConnection=True
    )
    await client[DATABASE][fixture.collection].insert_one({"_id": "one", "mongo_only": "yes"})

    with pymssql.connect(
        server=test_settings.sqlserver_host,
        port=str(test_settings.sqlserver_port),
        user=test_settings.sqlserver_user,
        password=test_settings.sqlserver_password.get_secret_value(),
        database=test_settings.sqlserver_database,
        login_timeout=10,
        timeout=10,
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE [dbo].[{fixture.table}] (sql_only NVARCHAR(16) NOT NULL)")
    try:
        yield fixture
    finally:
        await client[DATABASE][fixture.collection].drop()
        await client.close()
        with pymssql.connect(
            server=test_settings.sqlserver_host,
            port=str(test_settings.sqlserver_port),
            user=test_settings.sqlserver_user,
            password=test_settings.sqlserver_password.get_secret_value(),
            database=test_settings.sqlserver_database,
            login_timeout=10,
            timeout=10,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE [dbo].[{fixture.table}]")


@pytest_asyncio.fixture
async def routed(test_settings: Settings) -> AsyncIterator[SourceInspectionPort]:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value(), directConnection=True
    )
    try:
        yield build_routing_source_inspection_adapter(
            sources={},
            connectors={},
            overrides={
                MONGO_SOURCE: build_mongo_source_inspection_adapter(
                    client, database_name=DATABASE, source_id=MONGO_SOURCE
                ),
                SQL_SOURCE: build_sqlserver_source_inspection_adapter(
                    SqlServerConnectionSettings(
                        server=test_settings.sqlserver_host,
                        port=test_settings.sqlserver_port,
                        user=test_settings.sqlserver_user,
                        password=test_settings.sqlserver_password.get_secret_value(),
                        database=test_settings.sqlserver_database,
                        timeout_seconds=10,
                    ),
                    source_id=SQL_SOURCE,
                ),
            },
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_each_source_reaches_its_own_store_and_not_the_other(
    routed: SourceInspectionPort, two_stores: _Fixture
) -> None:
    """The whole reason per-source overrides exist. A misroute would report the
    Mongo collection as absent from Mongo, which reads as an empty database
    rather than as the wrong connector answering."""
    mongo_objects = {item.object_name for item in await routed.list_objects(source_id=MONGO_SOURCE)}
    sql_objects = {item.object_name for item in await routed.list_objects(source_id=SQL_SOURCE)}
    assert two_stores.collection in mongo_objects
    assert two_stores.collection not in sql_objects
    assert two_stores.sql_object in sql_objects
    assert two_stores.sql_object not in mongo_objects


@pytest.mark.asyncio
async def test_the_object_kind_reflects_the_store_that_answered(
    routed: SourceInspectionPort, two_stores: _Fixture
) -> None:
    """A second, independent signal that the right connector answered: only the
    Mongo adapter emits COLLECTION and only the SQL one emits TABLE."""
    mongo = await routed.describe_object(source_id=MONGO_SOURCE, object_name=two_stores.collection)
    sql = await routed.describe_object(source_id=SQL_SOURCE, object_name=two_stores.sql_object)
    assert mongo.object_kind is ObjectKind.COLLECTION
    assert sql.object_kind is ObjectKind.TABLE
    assert {field.field_name for field in mongo.fields} == {"mongo_only"}
    assert {field.field_name for field in sql.fields} == {"sql_only"}


@pytest.mark.asyncio
async def test_list_sources_reports_the_union_of_what_is_registered(
    routed: SourceInspectionPort,
) -> None:
    """Each adapter serves one source, so the routed view has to combine them --
    a caller that saw only one would think the other was unconfigured."""
    assert set(await routed.list_sources()) == {MONGO_SOURCE, SQL_SOURCE}


@pytest.mark.asyncio
async def test_an_unregistered_source_is_refused_rather_than_defaulted(
    routed: SourceInspectionPort,
) -> None:
    """Falling back to whichever connector happens to be registered is exactly
    how a source reads a store's worth of nothing and reports success."""
    with pytest.raises(UnreachableSource):
        await routed.list_objects(source_id="a_source_nobody_registered")
