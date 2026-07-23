"""Read-only unified physical inventory API for the Data Console."""

import asyncio
from typing import cast

from fastapi import APIRouter, Request
from neo4j import RoutingControl
from neo4j.exceptions import Neo4jError
from pydantic import Field

from return_platform.data_governance.inventory.contracts import (
    MongoDBInventory,
    SQLServerInventory,
)
from return_platform.data_governance.inventory.mongodb import (
    MongoDBInventoryError,
    get_mongodb_inventory,
)
from return_platform.data_governance.inventory.sqlserver import (
    SQLServerInventoryError,
    get_sqlserver_inventory,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import (
    APIResponse,
    ContractModel,
    DependencyErrorCode,
    ResponseMeta,
    WarningMeta,
)

router = APIRouter(prefix="/data-console/v1", tags=["Data Inventory"])

_LABELS_QUERY = "CALL db.labels() YIELD label RETURN label ORDER BY label"
_RELATIONSHIPS_QUERY = (
    "CALL db.relationshipTypes() YIELD relationshipType "
    "RETURN relationshipType ORDER BY relationshipType"
)


class Neo4jInventory(ContractModel):
    """Bounded graph structure observed through fixed metadata procedures."""

    labels: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    relationship_types: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)


class UnifiedInventory(ContractModel):
    """Partial-capable inventory across configured database engines."""

    sqlserver: SQLServerInventory | None = None
    mongodb: MongoDBInventory | None = None
    neo4j: Neo4jInventory | None = None


async def _get_neo4j_inventory(resources: RuntimeResources) -> Neo4jInventory:
    if resources.neo4j is None:
        raise RuntimeError("Neo4j inventory is unavailable.")
    async with asyncio.timeout(resources.settings.probe_timeout_seconds):
        label_result = await resources.neo4j.execute_query(
            _LABELS_QUERY,
            routing_=RoutingControl.READ,
        )
        relationship_result = await resources.neo4j.execute_query(
            _RELATIONSHIPS_QUERY,
            routing_=RoutingControl.READ,
        )
    labels = tuple(str(record["label"]) for record in label_result.records)
    relationships = tuple(str(record["relationshipType"]) for record in relationship_result.records)
    return Neo4jInventory(labels=labels, relationship_types=relationships)


def _warning(source: str, error: BaseException) -> WarningMeta:
    if isinstance(error, (MongoDBInventoryError, SQLServerInventoryError)):
        code = error.code.value
        message = str(error)
    elif isinstance(error, TimeoutError):
        code = DependencyErrorCode.TIMEOUT.value
        message = f"{source} metadata inventory timed out."
    elif isinstance(error, Neo4jError):
        code = DependencyErrorCode.QUERY_FAILED.value
        message = "Neo4j metadata inventory failed."
    else:
        code = DependencyErrorCode.UNINITIALIZED.value
        message = f"{source} metadata inventory is unavailable."
    return WarningMeta(source=source, code=code, message=message)


@router.get("/inventory", response_model=APIResponse[UnifiedInventory])
async def get_unified_inventory(request: Request) -> APIResponse[UnifiedInventory]:
    """Return metadata only, preserving healthy engines when another fails."""
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        warning = _warning("INVENTORY", RuntimeError("resources unavailable"))
        return APIResponse(
            data=UnifiedInventory(),
            meta=ResponseMeta(
                request_id=cast(str, request.state.correlation_id),
                partial=True,
                warnings=(warning,),
            ),
        )
    resources = resources_value
    settings = resources.settings

    async def sqlserver_inventory() -> SQLServerInventory:
        return await get_sqlserver_inventory(
            host=settings.sqlserver_host,
            port=settings.sqlserver_port,
            user=settings.sqlserver_user,
            password=settings.sqlserver_password.get_secret_value(),
            database=settings.sqlserver_database,
            timeout_seconds=settings.probe_timeout_seconds,
            executor=resources.sql_manager.executor,
        )

    async def mongodb_inventory() -> MongoDBInventory:
        if resources.mongo is None:
            raise RuntimeError("MongoDB inventory is unavailable.")
        return await get_mongodb_inventory(
            client=resources.mongo,
            timeout_seconds=settings.probe_timeout_seconds,
        )

    results = await asyncio.gather(
        sqlserver_inventory(),
        mongodb_inventory(),
        _get_neo4j_inventory(resources),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result

    warnings: list[WarningMeta] = []
    values: list[object | None] = []
    for source, result in zip(("SQLSERVER", "MONGODB", "NEO4J"), results, strict=True):
        if isinstance(result, BaseException):
            warnings.append(_warning(source, result))
            values.append(None)
        else:
            values.append(result)

    return APIResponse(
        data=UnifiedInventory(
            sqlserver=cast(SQLServerInventory | None, values[0]),
            mongodb=cast(MongoDBInventory | None, values[1]),
            neo4j=cast(Neo4jInventory | None, values[2]),
        ),
        meta=ResponseMeta(
            request_id=cast(str, request.state.correlation_id),
            partial=bool(warnings),
            warnings=tuple(warnings),
        ),
    )
