"""Re-export shim -- the real implementation moved to `source_connectors.mongodb`
(Phase 8 / Wave C1). Kept so existing importers under `dynamic_knowledge`
don't need to change; new code should import from `source_connectors.mongodb`
directly. Delete this shim once a repo-wide grep finds zero remaining
references to this path.
"""

from __future__ import annotations

from return_platform.source_connectors.mongodb import (
    CAPABILITIES,
    MongoConnectorError,
    MongoDBSourceScanConnector,
    SeedPin,
    fetch_one,
    sample_documents,
)

__all__ = [
    "CAPABILITIES",
    "MongoConnectorError",
    "MongoDBSourceScanConnector",
    "SeedPin",
    "fetch_one",
    "sample_documents",
]
