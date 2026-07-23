# Ownership and Write-Policy Matrix

| System | Ownership | UI Access |
|---|---|---|
| **SQL Server / OMC** | Authoritative business facts | Read-only browsing and evidence; no direct writes |
| **Source MongoDB** | Read-only discovery data | Read-only browsing; no workflow fields and no direct writes |
| **Neo4j** | Derived, rebuildable graph projection | Read-only exploration/evidence; rebuild or synchronization actions only through explicit backend commands |
| **Platform MongoDB** | Authoritative internal platform state | Read through bounded backend APIs; writes only through explicit platform use cases |
| **Temporal** | Execution and timers, not business-state ownership | Read execution status through backend APIs; never edit Temporal state directly |
| **Sandbox workspace** | Isolated developer/demo state | Writable only through explicit sandbox APIs with audit metadata and concurrency controls |
