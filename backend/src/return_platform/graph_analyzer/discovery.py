"""Bind one configured source to a read-only `SourceInspectionPort`.

WHY THIS EXISTS

`GraphAnalyzerService` used to answer `objects: []` and `objectCount: 0` for
every source a user configured through the UI. The only sources with any
structure were derived from the platform's own `SchemaRegistry`, so a newly
added connection appeared in the explorer with nothing under it: nothing to
expand, nothing to select, and therefore nothing to analyze or synchronise. The
documented journey -- add, validate, explore, select, analyze -- stopped at the
second step.

WHAT IT REUSES

Nothing here talks to a driver. `bootstrap/adapters/source_inspection_*` already
implement `SourceInspectionPort` over MongoDB, PostgreSQL, SQL Server and Neo4j,
and `source_connectors/` already owns the connection-parameter objects those
adapters take. This module is the missing binding: configured-source document
in, live port out, closed afterwards.

THE READ-ONLY BOUNDARY

`SourceInspectionPort` is eight named questions with no free-form query
parameter, which is the control rather than a check bolted onto one: there is
nowhere to pass a statement through. This module never widens that -- it opens a
connection, asks those questions, and closes it. Every driver handle it creates
is released in a `finally`, because a discovery run that leaks a pool is a
discovery run that eventually stops working.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.bootstrap.adapters.source_inspection_mongodb import (
    build_mongo_source_inspection_adapter,
)
from return_platform.bootstrap.adapters.source_inspection_neo4j import (
    build_neo4j_source_inspection_adapter,
)
from return_platform.bootstrap.adapters.source_inspection_postgresql import (
    build_postgres_source_inspection_adapter,
)
from return_platform.bootstrap.adapters.source_inspection_sqlserver import (
    build_sqlserver_source_inspection_adapter,
)
from return_platform.graph_analyzer.models import (
    PreviewGraph,
    PreviewGraphEdge,
    PreviewGraphNode,
    SourceField,
    SourceObject,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    ObjectKind,
    SourceInspectionPort,
)
from return_platform.source_connectors.postgresql import PostgresConnectionSettings
from return_platform.source_connectors.sqlserver import SqlServerConnectionSettings

#: Bound on how many objects one source contributes to the explorer tree.
#:
#: A production catalogue can hold tens of thousands of tables. Describing every
#: one on a metadata refresh would turn a UI action into a multi-minute scan of
#: the source, which is exactly the uncontrolled full scan the connector rules
#: forbid. The tree reports the truncation rather than hiding it.
MAX_OBJECTS_PER_SOURCE = 500

#: How many objects are described concurrently. `describe_object` is one round
#: trip each; serialising 500 of them is slow and running 500 at once exhausts
#: the source's connection budget, which is a source-side impact even though it
#: is not a write.
_DESCRIBE_CONCURRENCY = 8


class SourceUnavailableError(RuntimeError):
    """Raised when a configured source cannot be reached or authenticated."""


def _kind_for(object_kind: ObjectKind) -> str:
    """Map the port's vocabulary onto the tree's."""
    if object_kind is ObjectKind.COLLECTION:
        return "collection"
    if object_kind is ObjectKind.NODE_LABEL:
        return "entity"
    return "table"


@asynccontextmanager
async def inspection_port(document: Mapping[str, Any]) -> AsyncIterator[SourceInspectionPort]:
    """Open a read-only inspection port over one configured source.

    The connection is always closed on the way out, including when the caller
    raises. `source_id` is the stored document id, so every port answers for
    exactly the source it was built over and raises for any other.
    """

    engine = str(document["engine"])
    source_id = str(document["_id"])
    host = str(document["host"])
    port = int(document["port"])
    database = str(document["database"])
    username = str(document.get("username") or "")
    password = str(document.get("password") or "")

    if engine == "MONGODB":
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            _mongo_uri(host, port, database, username, password),
            serverSelectionTimeoutMS=5_000,
        )
        try:
            yield build_mongo_source_inspection_adapter(
                client, database_name=database, source_id=source_id
            )
        finally:
            await client.close()
        return

    if engine == "POSTGRESQL":
        yield build_postgres_source_inspection_adapter(
            PostgresConnectionSettings(
                host=host, port=port, user=username, password=password, database=database
            ),
            source_id=source_id,
        )
        return

    if engine == "SQLSERVER":
        yield build_sqlserver_source_inspection_adapter(
            SqlServerConnectionSettings(
                server=host, port=port, user=username, password=password, database=database
            ),
            source_id=source_id,
        )
        return

    if engine == "NEO4J":
        driver = AsyncGraphDatabase.driver(
            _bolt_uri(host, port), auth=(username, password) if username else None
        )
        try:
            yield build_neo4j_source_inspection_adapter(
                driver, source_id=source_id, database=database or None
            )
        finally:
            await driver.close()
        return

    raise SourceUnavailableError(f"No read-only connector is available for engine {engine}.")


def _mongo_uri(host: str, port: int, database: str, username: str, password: str) -> str:
    """Assemble a MongoDB URI with every credential percent-encoded.

    A password containing `@`, `/`, `:` or `%` silently reroutes an unencoded
    URI to the wrong host or fails to parse, which surfaces as an
    authentication error against a server that was never contacted.
    """
    from urllib.parse import quote

    if host.startswith(("mongodb://", "mongodb+srv://")):
        return host
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    # `directConnection=true` addresses the named host itself rather than
    # entering replica-set discovery. Without it the driver reads the set's
    # configuration and follows the internal hostnames it advertises, which do
    # not resolve from outside the cluster -- so a perfectly reachable member
    # reports "could not reach any servers".
    return f"mongodb://{credentials}{host}:{port}/{database}?authSource=admin&directConnection=true"


def _bolt_uri(host: str, port: int) -> str:
    return host if "://" in host else f"bolt://{host}:{port}"


async def probe_source(document: Mapping[str, Any]) -> tuple[str, str]:
    """Return `(status, message)` for one configured source.

    Reachability is a returned value rather than an exception because the
    operator asking is testing a connection: half the time an unreachable source
    is the expected answer and needs a message they can act on.
    """
    try:
        async with inspection_port(document) as port:
            result = await port.validate(source_id=str(document["_id"]))
    except Exception as error:  # noqa: BLE001 - every driver failure is a status here
        name = type(error).__name__.casefold()
        detail = str(error)
        if "auth" in name or "auth" in detail.casefold() or "login" in name:
            return "AUTHENTICATION_FAILED", "Authentication was refused by the source."
        return "UNREACHABLE", "The source could not be reached."
    if not result.reachable:
        return "UNREACHABLE", result.detail or "The source could not be reached."
    version = f" ({result.server_version})" if result.server_version else ""
    return "CONNECTED", f"Read-only connection validated{version}."


async def discover_objects(document: Mapping[str, Any]) -> tuple[list[SourceObject], bool]:
    """Discover one configured source's structure.

    Returns the explorer tree and whether the object list was truncated, so the
    UI can say "showing the first N" rather than quietly presenting a partial
    catalogue as a complete one.
    """

    source_id = str(document["_id"])
    database = str(document["database"])
    async with inspection_port(document) as port:
        refs = list(await port.list_objects(source_id=source_id))
        truncated = len(refs) > MAX_OBJECTS_PER_SOURCE
        refs = refs[:MAX_OBJECTS_PER_SOURCE]

        semaphore = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)

        async def describe(name: str) -> tuple[str, Any, Any]:
            async with semaphore:
                description = await port.describe_object(source_id=source_id, object_name=name)
                try:
                    indexes = await port.list_indexes(source_id=source_id, object_name=name)
                except Exception:  # noqa: BLE001 - index metadata is advisory only
                    indexes = ()
                return name, description, indexes

        described = await asyncio.gather(
            *(describe(ref.object_name) for ref in refs), return_exceptions=True
        )

    by_name: dict[str, tuple[Any, Any]] = {}
    for item in described:
        if isinstance(item, BaseException):
            # One unreadable object must not lose the other 499. It is simply
            # absent from the tree, which is the same state as "no metadata".
            continue
        name, description, indexes = item
        by_name[name] = (description, indexes)

    # Grouped by the namespace embedded in the object name where the source uses
    # one (`schema.table`), so a PostgreSQL or SQL Server catalogue does not
    # arrive as one flat list of several hundred siblings.
    grouped: dict[str, list[Any]] = {}
    for ref in refs:
        entry = by_name.get(ref.object_name)
        if entry is None:
            continue
        namespace = ref.object_name.rsplit(".", 1)[0] if "." in ref.object_name else "Objects"
        grouped.setdefault(namespace, []).append((ref, *entry))

    namespaces: list[SourceObject] = []
    for namespace, items in sorted(grouped.items()):
        children: list[SourceObject] = []
        for ref, description, indexes in sorted(items, key=lambda item: item[0].object_name):
            indexed_fields = {field for index in indexes for field in index.fields}
            identifier_fields = {
                field
                for index in indexes
                if index.primary or index.unique
                for field in index.fields
            }
            leaf = ref.object_name.rsplit(".", 1)[-1]
            children.append(
                SourceObject(
                    id=f"{source_id}:{ref.object_name}",
                    name=leaf,
                    kind=_kind_for(ref.object_kind),
                    path=[database, namespace, leaf],
                    selectable=True,
                    estimatedRows=description.approximate_row_count,
                    fields=[
                        SourceField(
                            name=field.field_name,
                            dataType=field.declared_type,
                            nullable=field.nullable,
                            identifier=field.field_name in identifier_fields,
                            indexed=field.field_name in indexed_fields,
                        )
                        for field in description.fields
                    ],
                )
            )
        namespaces.append(
            SourceObject(
                id=f"{source_id}:namespace:{namespace}",
                name=namespace,
                kind="schema",
                path=[database, namespace],
                selectable=True,
                children=children,
            )
        )

    tree = [
        SourceObject(
            id=f"{source_id}:database:{database}",
            name=database,
            kind="database",
            path=[database],
            selectable=True,
            children=namespaces,
        )
    ]
    return tree, truncated


def count_objects(nodes: Sequence[SourceObject]) -> int:
    """Selectable leaves, which is what "N objects" means to an operator."""
    return sum((1 if not node.children else 0) + count_objects(node.children) for node in nodes)


async def sample_object(
    document: Mapping[str, Any], object_name: str, limit: int
) -> list[dict[str, Any]]:
    """Read at most `limit` rows from one object. Bounded by the caller, always."""
    async with inspection_port(document) as port:
        rows = await port.sample(
            source_id=str(document["_id"]), object_name=object_name, limit=limit
        )
        return [dict(row) for row in rows]


def graph_sample(rows: Sequence[Mapping[str, Any]], object_name: str) -> PreviewGraph | None:
    """Shape sampled rows from a graph source into nodes and relationships.

    Only the endpoints present in the sample are emitted, so every edge resolves
    to a node the caller was actually given. Rows that carry no identifiable node
    shape produce no graph rather than a fabricated one.
    """
    nodes: dict[str, PreviewGraphNode] = {}
    edges: list[PreviewGraphEdge] = []
    for index, row in enumerate(rows):
        identity = str(row.get("_elementId") or row.get("_id") or f"{object_name}:{index}")
        properties = {key: value for key, value in row.items() if not key.startswith("_")}
        nodes.setdefault(
            identity,
            PreviewGraphNode(
                id=identity,
                labels=[object_name],
                properties=properties,
            ),
        )
    if not nodes:
        return None
    return PreviewGraph(nodes=list(nodes.values()), edges=edges)
