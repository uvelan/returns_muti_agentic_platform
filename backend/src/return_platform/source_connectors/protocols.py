"""Canonical read-only source connector protocols.

`SourceScanConnector`/`SourceScanRegistry` moved unchanged from
`dynamic_knowledge.sync.coordinator` (previously defined inline there);
`TargetedSourceConnector`/`ConnectorRegistry` moved unchanged from
`dynamic_knowledge.on_demand_sync.coordinator` (Phase 8 / Wave C1).
`PointLookupConnector` is new, additive: a bounded single-document/row lookup
kept as its own protocol rather than folded into `SourceScanConnector`,
matching `SourceConnectorCapabilities`'s existing philosophy that no
capability is assumed uniformly true of every source.

No mutation method exists on any protocol in this module -- external sources
are read-only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.contracts import (
    CursorComparison,
    DatasetRef,
    LogicalTargetedReadPlan,
    RawSourceDocument,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)


class SourceScanConnector(Protocol):
    def capabilities(self) -> SourceConnectorCapabilities: ...

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor: ...

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison: ...

    def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]: ...


class SourceScanRegistry(Protocol):
    def resolve(self, source_asset_id: str) -> SourceScanConnector: ...


class TargetedSourceConnector(Protocol):
    async def targeted_read(
        self,
        *,
        schema: ActiveSchema,
        plan: LogicalTargetedReadPlan,
    ) -> RawSourcePage: ...


class ConnectorRegistry(Protocol):
    def resolve(self, source_asset_id: str) -> TargetedSourceConnector: ...


class PointLookupConnector(Protocol):
    """A bounded single-document/row lookup by exact key -- no scan, no cursor.

    For consumers with an already-resolved physical location and key (a
    customer-lookup adapter, an admin single-record preview), not every
    connector needs to implement this.
    """

    async def fetch_one(
        self, *, ref: DatasetRef, key: Mapping[str, Any]
    ) -> RawSourceDocument | None: ...
