"""One `SourceInspectionPort` over many sources, dispatched by connector type.

Uses the existing `SourceConnectorsByType` rather than growing a third registry
beside it. That class already answers "what kind of thing is this source and who
speaks it" for scans and for targeted reads, and it is generic over the connector
protocol precisely so a new capability is a new type argument, not a new file --
the alternative is what its own docstring describes: two hand-written MONGODB /
MSSQL branches that drift until a source syncs on a schedule and cannot be
inspected, discovered in production.

Per-source `overrides` is how a backend connector bound to one database is
registered, and it is the normal case here rather than the exception: an
inspection adapter is constructed against a specific client and database, and
two MongoDB sources in different stores must not resolve to each other's
connector. The by-type mapping stays for the case a deployment does have one
connector per type.

`UnreachableSource` is left to propagate. A caller that cannot reach a source has
nothing useful to do with a `None`, and the alternative -- skipping it -- is how
an analysis quietly covers less than it claims.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from return_platform.dynamic_knowledge.schema import ConnectorType, SourceAssetDefinition
from return_platform.graph_schema_analyzer.ports.source_port import (
    IndexDescription,
    ObjectDescription,
    ObjectProfile,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)
from return_platform.source_connectors.registry import SourceConnectorsByType

__all__ = ["RoutingSourceInspectionAdapter", "build_routing_source_inspection_adapter"]


class RoutingSourceInspectionAdapter:
    """Structurally satisfies `SourceInspectionPort` across every configured source."""

    def __init__(self, registry: SourceConnectorsByType[SourceInspectionPort]) -> None:
        self._registry = registry

    async def validate(self, *, source_id: str) -> SourceValidation:
        return await self._registry.resolve(source_id).validate(source_id=source_id)

    async def list_sources(self) -> Sequence[str]:
        """The union of what every registered connector can reach, deduplicated
        and ordered so a caller comparing two listings sees a stable answer."""
        seen: list[str] = []
        for connector in self._registry.connectors():
            for source_id in await connector.list_sources():
                if source_id not in seen:
                    seen.append(source_id)
        return tuple(sorted(seen))

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        return await self._registry.resolve(source_id).list_objects(source_id=source_id)

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        return await self._registry.resolve(source_id).describe_object(
            source_id=source_id, object_name=object_name
        )

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return await self._registry.resolve(source_id).sample(
            source_id=source_id, object_name=object_name, limit=limit, fields=fields
        )

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        return await self._registry.resolve(source_id).profile(
            source_id=source_id, object_name=object_name, sample_size=sample_size
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        return await self._registry.resolve(source_id).list_indexes(
            source_id=source_id, object_name=object_name
        )

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        return await self._registry.resolve(source_id).list_relationships(
            source_id=source_id, object_name=object_name
        )


def build_routing_source_inspection_adapter(
    *,
    sources: Mapping[str, SourceAssetDefinition],
    connectors: Mapping[ConnectorType, SourceInspectionPort | None],
    overrides: Mapping[str, SourceInspectionPort] | None = None,
) -> SourceInspectionPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    registry: SourceConnectorsByType[SourceInspectionPort] = SourceConnectorsByType(
        sources=sources, connectors=connectors, overrides=overrides
    )
    return RoutingSourceInspectionAdapter(registry)
