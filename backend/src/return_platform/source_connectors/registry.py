"""One dispatch from a source asset to the connector that can reach it.

Two of these existed: `dynamic_knowledge.sync.adapters.SourceConnectorRegistry`
for full/incremental scans and
`dynamic_knowledge.integration.on_demand_sync_adapters.OnDemandConnectorRegistry`
for targeted reads. They differed only in the protocol they returned, and both
hand-wrote the same MONGODB/MSSQL branch -- so adding a third source type meant
editing two files, and forgetting one meant a source that syncs on a schedule
but cannot be reached on demand (or the reverse), discovered in production.

**Which sources exist is configuration, so which connector serves one is a
lookup, not a branch.** A source asset declares its `connector_type`; this maps
that type to the single connector instance registered for it. Nothing here knows
what a sales order is, and nothing here grows a case per source.

Generic over the connector protocol rather than fixed to one, because a scan and
a targeted read are genuinely different capabilities -- `SourceScanConnector` and
`TargetedSourceConnector` -- and a registry that returned "some connector" would
push the distinction back onto every caller as a cast.
"""

from __future__ import annotations

from collections.abc import Mapping

from return_platform.dynamic_knowledge.schema import ConnectorType, SourceAssetDefinition

__all__ = ["SourceConnectorsByType", "UnreachableSource"]


class UnreachableSource(RuntimeError):
    """A source asset exists in configuration and no connector can reach it.

    Raised rather than returned as `None`: a caller that cannot reach a source
    has nothing useful to do with that fact, and the alternative -- skipping the
    source -- is how a sync silently covers less than it claims.
    """


class SourceConnectorsByType[ConnectorT]:
    """Source asset id -> the connector registered for its connector type.

    Holds the source catalogue rather than the whole `ActiveSchema` so that a
    caller resolving a dataset through the source-binding catalogue
    (`dynamic_knowledge.source_bindings`) can pass the resolved assets directly:
    the registry's question is "what kind of thing is this and who speaks it",
    which neither the graph shape nor the agent policies have any part in.
    """

    def __init__(
        self,
        *,
        sources: Mapping[str, SourceAssetDefinition],
        connectors: Mapping[ConnectorType, ConnectorT | None],
    ) -> None:
        self._sources = dict(sources)
        # `None` entries are dropped here rather than at resolve time so that
        # "configured but unavailable" and "never supported" produce the same
        # message: from the caller's side they are the same outage.
        self._connectors = {
            connector_type: connector
            for connector_type, connector in connectors.items()
            if connector is not None
        }

    def resolve(self, source_asset_id: str) -> ConnectorT:
        source = self._sources.get(source_asset_id)
        if source is None:
            raise UnreachableSource(
                f"source {source_asset_id!r} is not in the active schema's source catalogue"
            )
        connector = self._connectors.get(source.connector_type)
        if connector is None:
            raise UnreachableSource(
                f"source {source_asset_id!r} is {source.connector_type.value} and no connector "
                f"of that type is configured for this operation "
                f"(configured: {sorted(item.value for item in self._connectors)})"
            )
        return connector
