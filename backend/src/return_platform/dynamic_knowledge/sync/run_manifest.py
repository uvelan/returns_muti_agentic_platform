"""Full-sync run manifest: what a run intends to do, captured before any scan
begins, so a run's watermark bounds are always fixed up front rather than
drifting later-source-sees-more-than-earlier-source across the same run.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.on_demand_sync.contracts import SourceCursor


class SourceWatermarkRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_asset_id: str
    captured_watermark: SourceCursor
    connector_version: str


class SyncRunManifest(BaseModel):
    """Persisted once, before scanning starts, then updated twice more (scan
    complete, reconciliation complete) -- never re-derived from partial state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_run_id: str
    graph_generation_id: str
    schema_fingerprint: str
    source_watermarks: tuple[SourceWatermarkRecord, ...]


class RunManifestRecorder(Protocol):
    async def record_watermarks(self, manifest: SyncRunManifest) -> None: ...

    async def record_scan_completed(self, sync_run_id: str) -> None: ...

    async def record_reconciliation_completed(self, sync_run_id: str) -> None: ...
