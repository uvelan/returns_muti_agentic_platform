"""Re-export shim -- the real implementation moved to `source_connectors.sqlserver`
(Phase 8 / Wave C1). Kept so existing importers under `dynamic_knowledge`
don't need to change; new code should import from `source_connectors.sqlserver`
directly. Delete this shim once a repo-wide grep finds zero remaining
references to this path.

Re-exports `pymssql` itself (not just the connector classes) because
`tests/dynamic_knowledge/test_sqlserver_connector.py` monkeypatches
`sqlserver_module.pymssql.connect` -- since `pymssql` is one shared module
object regardless of which importer references it, patching it here also
patches what `source_connectors.sqlserver`'s real implementation calls.
"""

from __future__ import annotations

import pymssql

from return_platform.source_connectors.sqlserver import (
    CAPABILITIES,
    SqlServerConnectionSettings,
    SqlServerConnectorError,
    SqlServerSourceScanConnector,
    fetch_row,
    sample_rows,
)

__all__ = [
    "CAPABILITIES",
    "SqlServerConnectionSettings",
    "SqlServerConnectorError",
    "SqlServerSourceScanConnector",
    "fetch_row",
    "pymssql",
    "sample_rows",
]
