"""Read-only Data Console APIs for exploring the Neo4j Graph."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.settings import Settings
from return_platform.data_console.api.auth import require_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta, WarningMeta

__all__ = [
    "GraphExplorerService",
    "GraphExplorerServicePort",
    "resolve_graph_explorer_service",
    "router",
]

router = APIRouter(
    prefix="/data-console/v1/graph",
    tags=["Graph Explorer"],
)

_READ_ROLES: Final = frozenset({"console_admin", "console_viewer", "graph_explorer"})
_SOURCE: Final = "GRAPH_EXPLORER"


class GraphNode(BaseModel):
    """Canonical representation of a Graph Node."""

    model_config = ConfigDict(extra="forbid")
    id: str
    labels: list[str]
    properties: dict[str, Any]
    truncated: bool = False
    provenance: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None


class GraphRelationship(BaseModel):
    """Canonical representation of a Graph Relationship."""

    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    startNodeId: str
    endNodeId: str
    properties: dict[str, Any]
    truncated: bool = False


class GraphExpansionLimit(BaseModel):
    """Metadata about graph expansion limits."""

    model_config = ConfigDict(extra="forbid")
    maxNodes: int
    maxRelationships: int
    maxDepth: int
    expansionLimit: int


class GraphSearchResultData(BaseModel):
    """Payload for Graph Search Results."""

    model_config = ConfigDict(extra="forbid")
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]


class GraphSearchMeta(ResponseMeta):
    """Metadata for Graph Search Results."""

    limits: GraphExpansionLimit | None = None
    isTruncated: bool = False
    isPartial: bool = False


class GraphSearchResult(BaseModel):
    """Top-level Graph Search Result."""

    model_config = ConfigDict(extra="forbid")
    data: GraphSearchResultData
    page: Any | None = None
    meta: GraphSearchMeta


class _GraphExplorerResolutionError(RuntimeError):
    """Raised when request-scoped graph-explorer resources are unavailable."""


class GraphExplorerServicePort:
    """Read-only application-service boundary used by the API."""

    async def search_exact_id(self, q: str, expansion_depth: int) -> GraphSearchResultData:
        """Return a bounded graph search result for an exact identity."""
        raise NotImplementedError

    async def get_node(self, node_id: str) -> GraphNode | None:
        """Return a specific graph node."""
        raise NotImplementedError

    async def get_relationship(self, relationship_id: str) -> GraphRelationship | None:
        """Return a specific graph relationship."""
        raise NotImplementedError

    async def expand_neighborhood(
        self, node_id: str, expansion_depth: int
    ) -> GraphSearchResultData:
        """Return a node's expanded neighborhood."""
        raise NotImplementedError


class GraphExplorerService(GraphExplorerServicePort):
    """Read-only application service for graph exploration."""

    def __init__(self, driver: AsyncDriver, database: str) -> None:
        self._driver = driver
        self._database = database

    async def _run_query(self, query: str, parameters: dict[str, Any] | None = None) -> list[Any]:
        async with self._driver.session(
            database=self._database, default_access_mode="READ"
        ) as session:
            result = await session.run(query, parameters or {})
            return [record async for record in result]

    def _extract_node(self, node: Any) -> GraphNode:
        return GraphNode(
            id=str(node.element_id), labels=list(node.labels), properties=dict(node.items())
        )

    def _extract_relationship(self, rel: Any) -> GraphRelationship:
        return GraphRelationship(
            id=str(rel.element_id),
            type=rel.type,
            startNodeId=str(rel.start_node.element_id),
            endNodeId=str(rel.end_node.element_id),
            properties=dict(rel.items()),
        )

    async def search_exact_id(self, q: str, expansion_depth: int) -> GraphSearchResultData:
        depth = max(0, min(expansion_depth, 3))

        # Match nodes exactly by element_id or common key fields
        query = """
        MATCH (n)
        WHERE elementId(n) = $q OR n.id = $q OR n.customer_id = $q OR n.customer_account_id = $q
        RETURN n
        LIMIT 50
        """
        records = await asyncio.wait_for(self._run_query(query, {"q": q}), timeout=10.0)

        nodes: dict[str, GraphNode] = {}
        relationships: dict[str, GraphRelationship] = {}

        for record in records:
            n = self._extract_node(record["n"])
            nodes[n.id] = n

        if depth > 0 and nodes:
            # Expand relationships if depth requested
            node_ids = list(nodes.keys())
            # For simplicity, Phase 3 implements 1-hop exact expansion
            expand_query = """
            MATCH (n)-[r]-(m)
            WHERE elementId(n) IN $node_ids
            RETURN n, r, m
            LIMIT 200
            """
            expand_records = await asyncio.wait_for(
                self._run_query(expand_query, {"node_ids": node_ids}), timeout=10.0
            )
            for record in expand_records:
                n = self._extract_node(record["n"])
                m = self._extract_node(record["m"])
                r = self._extract_relationship(record["r"])
                nodes[n.id] = n
                nodes[m.id] = m
                relationships[r.id] = r

        return GraphSearchResultData(
            nodes=list(nodes.values()), relationships=list(relationships.values())
        )

    async def get_node(self, node_id: str) -> GraphNode | None:
        query = "MATCH (n) WHERE elementId(n) = $node_id RETURN n"
        records = await asyncio.wait_for(self._run_query(query, {"node_id": node_id}), timeout=10.0)
        if not records:
            return None
        return self._extract_node(records[0]["n"])

    async def get_relationship(self, relationship_id: str) -> GraphRelationship | None:
        query = "MATCH ()-[r]->() WHERE elementId(r) = $rel_id RETURN r"
        records = await asyncio.wait_for(
            self._run_query(query, {"rel_id": relationship_id}), timeout=10.0
        )
        if not records:
            return None
        return self._extract_relationship(records[0]["r"])

    async def expand_neighborhood(
        self, node_id: str, expansion_depth: int
    ) -> GraphSearchResultData:
        # Same logic as 1-hop expansion for now
        query = """
        MATCH (n)-[r]-(m)
        WHERE elementId(n) = $node_id
        RETURN n, r, m
        LIMIT 200
        """
        records = await asyncio.wait_for(self._run_query(query, {"node_id": node_id}), timeout=10.0)

        nodes: dict[str, GraphNode] = {}
        relationships: dict[str, GraphRelationship] = {}

        for record in records:
            n = self._extract_node(record["n"])
            m = self._extract_node(record["m"])
            r = self._extract_relationship(record["r"])
            nodes[n.id] = n
            nodes[m.id] = m
            relationships[r.id] = r

        # Ensure the source node is included even if it has no relationships
        if not nodes:
            node = await self.get_node(node_id)
            if node:
                nodes[node.id] = node

        return GraphSearchResultData(
            nodes=list(nodes.values()), relationships=list(relationships.values())
        )


def resolve_graph_explorer_service(request: Request) -> GraphExplorerServicePort:
    resources_value = getattr(request.app.state, "resources", None)
    settings_value = getattr(request.app.state, "settings", None)
    if not isinstance(resources_value, RuntimeResources):
        raise _GraphExplorerResolutionError
    if not isinstance(settings_value, Settings):
        raise _GraphExplorerResolutionError
    if resources_value.neo4j is None:
        raise _GraphExplorerResolutionError

    return GraphExplorerService(
        driver=resources_value.neo4j, database=settings_value.neo4j_database
    )


def _request_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _response_meta(request: Request, warnings: list[WarningMeta] | None = None) -> ResponseMeta:
    return ResponseMeta(
        request_id=_request_id(request),
        partial=bool(warnings),
        warnings=tuple(warnings) if warnings else (),
    )


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    payload = APIResponse[None](
        data=None,
        meta=ResponseMeta(
            request_id=_request_id(request),
            partial=True,
            warnings=(WarningMeta(source=_SOURCE, code=code, message=message),),
        ),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _unavailable_response(request: Request) -> JSONResponse:
    return _error_response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "DEPENDENCY_UNAVAILABLE",
        "Neo4j database is unavailable.",
    )


def _neo4j_error_response(request: Request, error: Neo4jError) -> JSONResponse:
    return _error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "QUERY_FAILED",
        "The graph query failed to execute.",
    )


def _timeout_error_response(request: Request) -> JSONResponse:
    return _error_response(
        request,
        status.HTTP_504_GATEWAY_TIMEOUT,
        "QUERY_TIMEOUT",
        "The graph query timed out.",
    )


@router.get("/search", response_model=GraphSearchResult)
async def search_graph(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=128)],
    expansionDepth: Annotated[int, Query(ge=0, le=3)] = 1,
    user_id: str = Depends(require_roles(_READ_ROLES)),
) -> APIResponse[GraphSearchResultData] | JSONResponse:
    try:
        service = resolve_graph_explorer_service(request)
        data = await service.search_exact_id(q, expansionDepth)
    except _GraphExplorerResolutionError:
        return _unavailable_response(request)
    except TimeoutError:
        return _timeout_error_response(request)
    except Neo4jError as error:
        return _neo4j_error_response(request, error)

    return GraphSearchResult(
        data=data,
        page=None,
        meta=GraphSearchMeta(
            request_id=_request_id(request),
            limits=GraphExpansionLimit(
                maxNodes=200, maxRelationships=200, maxDepth=3, expansionLimit=expansionDepth
            ),
        ),
    )


@router.get("/nodes/{node_id}", response_model=APIResponse[GraphNode])
async def get_graph_node(
    request: Request,
    node_id: Annotated[str, Path(min_length=1, max_length=256)],
    user_id: str = Depends(require_roles(_READ_ROLES)),
) -> APIResponse[GraphNode] | JSONResponse:
    try:
        service = resolve_graph_explorer_service(request)
        node = await service.get_node(node_id)
    except _GraphExplorerResolutionError:
        return _unavailable_response(request)
    except TimeoutError:
        return _timeout_error_response(request)
    except Neo4jError as error:
        return _neo4j_error_response(request, error)

    if node is None:
        raise HTTPException(status_code=404, detail="The requested graph entity was not found.")

    return APIResponse(data=node, meta=_response_meta(request))


@router.get("/relationships/{relationship_id}", response_model=APIResponse[GraphRelationship])
async def get_graph_relationship(
    request: Request,
    relationship_id: Annotated[str, Path(min_length=1, max_length=256)],
    user_id: str = Depends(require_roles(_READ_ROLES)),
) -> APIResponse[GraphRelationship] | JSONResponse:
    try:
        service = resolve_graph_explorer_service(request)
        rel = await service.get_relationship(relationship_id)
    except _GraphExplorerResolutionError:
        return _unavailable_response(request)
    except TimeoutError:
        return _timeout_error_response(request)
    except Neo4jError as error:
        return _neo4j_error_response(request, error)

    if rel is None:
        raise HTTPException(status_code=404, detail="The requested graph entity was not found.")

    return APIResponse(data=rel, meta=_response_meta(request))


@router.get("/nodes/{node_id}/neighborhood", response_model=GraphSearchResult)
async def expand_graph_neighborhood(
    request: Request,
    node_id: Annotated[str, Path(min_length=1, max_length=256)],
    expansionDepth: Annotated[int, Query(ge=1, le=3)] = 1,
    user_id: str = Depends(require_roles(_READ_ROLES)),
) -> APIResponse[GraphSearchResultData] | JSONResponse:
    try:
        service = resolve_graph_explorer_service(request)
        data = await service.expand_neighborhood(node_id, expansionDepth)
    except _GraphExplorerResolutionError:
        return _unavailable_response(request)
    except TimeoutError:
        return _timeout_error_response(request)
    except Neo4jError as error:
        return _neo4j_error_response(request, error)

    return GraphSearchResult(
        data=data,
        page=None,
        meta=GraphSearchMeta(
            request_id=_request_id(request),
            limits=GraphExpansionLimit(
                maxNodes=200, maxRelationships=200, maxDepth=3, expansionLimit=expansionDepth
            ),
        ),
    )
