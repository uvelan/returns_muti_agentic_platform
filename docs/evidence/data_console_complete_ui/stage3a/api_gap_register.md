# API Gap Register

| ID | Method | Exact Path | Module | Stage | Validation | Behavior | Mock Handler | Closure |
|---|---|---|---|---|---|---|---|---|
| `DC-API-SOURCES-LIST` | GET | `/data-console/v1/sources` | `data_console` | 3C | UNAVAILABLE | FIXTURE_MODE | `mock_getSources` | Implement API |
| `DC-API-SOURCES-DETAIL` | GET | `/data-console/v1/sources/{sourceId}` | `data_console` | 3C | UNAVAILABLE | FIXTURE_MODE | `mock_getSource` | Implement API |
| `DC-API-BROWSER-ASSETS` | GET | `/data-console/v1/browser/assets` | `data_console` | 3C | UNAVAILABLE | FIXTURE_MODE | `mock_getBrowserAssets` | Implement API |
| `DC-API-BROWSER-RECORDS` | GET | `/data-console/v1/browser/{engine}/{assetId}/records` | `data_console` | 3C | UNAVAILABLE | FIXTURE_MODE | `mock_getRecords` | Implement API |
| `DC-API-BROWSER-RECORD-DETAIL` | GET | `/data-console/v1/browser/{engine}/{assetId}/records/{recordId}` | `data_console` | 3C | UNAVAILABLE | FIXTURE_MODE | `mock_getRecord` | Implement API |
| `DC-API-GRAPH-SEARCH` | GET | `/data-console/v1/graph/search` | `data_console` | 3D | UNAVAILABLE | FIXTURE_MODE | `mock_searchGraph` | Implement API |
| `DC-API-GRAPH-NODE` | GET | `/data-console/v1/graph/nodes/{nodeId}` | `data_console` | 3D | UNAVAILABLE | FIXTURE_MODE | `mock_getNode` | Implement API |
| `DC-API-GRAPH-REL` | GET | `/data-console/v1/graph/relationships/{relationshipId}` | `data_console` | 3D | UNAVAILABLE | FIXTURE_MODE | `mock_getRelationship` | Implement API |
| `DC-API-GRAPH-EXPAND` | GET | `/data-console/v1/graph/nodes/{nodeId}/neighborhood` | `data_console` | 3D | UNAVAILABLE | FIXTURE_MODE | `mock_expandNeighborhood` | Implement API |
| `DC-API-WORKSPACES-LIST` | GET | `/data-console/v1/workspaces` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_getWorkspaces` | Implement API |
| `DC-API-WORKSPACES-CREATE` | POST | `/data-console/v1/workspaces` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_createWorkspace` | Implement API |
| `DC-API-WORKSPACES-DETAIL` | GET | `/data-console/v1/workspaces/{workspaceId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_getWorkspace` | Implement API |
| `DC-API-WORKSPACES-UPDATE` | PATCH | `/data-console/v1/workspaces/{workspaceId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_updateWorkspace` | Implement API |
| `DC-API-WORKSPACES-DELETE` | DELETE | `/data-console/v1/workspaces/{workspaceId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_deleteWorkspace` | Implement API |
| `DC-API-WORKSPACE-RECORDS-CREATE` | POST | `/data-console/v1/workspaces/{workspaceId}/records` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_createWorkspaceRecord` | Implement API |
| `DC-API-WORKSPACE-RECORDS-DETAIL` | GET | `/data-console/v1/workspaces/{workspaceId}/records/{recordId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_getWorkspaceRecord` | Implement API |
| `DC-API-WORKSPACE-RECORDS-UPDATE` | PATCH | `/data-console/v1/workspaces/{workspaceId}/records/{recordId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_updateWorkspaceRecord` | Implement API |
| `DC-API-WORKSPACE-RECORDS-DELETE` | DELETE | `/data-console/v1/workspaces/{workspaceId}/records/{recordId}` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_deleteWorkspaceRecord` | Implement API |
| `DC-API-IMPORTS-LIST` | GET | `/data-console/v1/imports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getImports` | Implement API |
| `DC-API-IMPORTS-CREATE` | POST | `/data-console/v1/imports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_createImport` | Implement API |
| `DC-API-IMPORTS-DETAIL` | GET | `/data-console/v1/imports/{jobId}` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getImport` | Implement API |
| `DC-API-EXPORTS-LIST` | GET | `/data-console/v1/exports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getExports` | Implement API |
| `DC-API-EXPORTS-CREATE` | POST | `/data-console/v1/exports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_createExport` | Implement API |
| `DC-API-EXPORTS-DETAIL` | GET | `/data-console/v1/exports/{jobId}` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getExport` | Implement API |
| `DC-API-EXPORTS-DOWNLOAD` | GET | `/data-console/v1/exports/{jobId}/download` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_downloadExport` | Implement API |
| `DC-API-JOBS-LIST` | GET | `/data-console/v1/jobs` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getJobs` | Implement API |
| `DC-API-JOBS-DETAIL` | GET | `/data-console/v1/jobs/{jobId}` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getJob` | Implement API |
| `DC-API-SCENARIOS-LIST` | GET | `/data-console/v1/scenarios` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_getScenarios` | Implement API |
| `DC-API-SCENARIOS-CREATE` | POST | `/data-console/v1/scenarios` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_createScenario` | Implement API |
| `DC-API-SCENARIOS-DETAIL` | GET | `/data-console/v1/scenarios/{scenarioId}` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_getScenario` | Implement API |
| `DC-API-SCENARIOS-GENERATE` | POST | `/data-console/v1/scenarios/{scenarioId}/generate` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_generateScenario` | Implement API |
| `DC-API-SCENARIOS-VALIDATE` | POST | `/data-console/v1/scenarios/{scenarioId}/validate` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_validateScenario` | Implement API |
| `DC-API-SCENARIOS-APPROVE` | POST | `/data-console/v1/scenarios/{scenarioId}/approve` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_approveScenario` | Implement API |
| `DC-API-SCENARIOS-PREVIEW` | GET | `/data-console/v1/scenarios/{scenarioId}/preview` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_previewScenario` | Implement API |
| `DC-API-AUDIT-LIST` | GET | `/data-console/v1/audit` | `data_console` | 3H | UNAVAILABLE | FIXTURE_MODE | `mock_getAuditList` | Implement API |
| `DC-API-AUDIT-DETAIL` | GET | `/data-console/v1/audit/{auditId}` | `data_console` | 3H | UNAVAILABLE | FIXTURE_MODE | `mock_getAuditDetail` | Implement API |
| `DC-API-GOVERNANCE` | GET | `/data-console/v1/governance` | `data_console` | 3H | UNAVAILABLE | FIXTURE_MODE | `mock_getGovernance` | Implement API |
| `DC-API-SETTINGS` | GET | `/data-console/v1/settings` | `data_console` | 3H | UNAVAILABLE | FIXTURE_MODE | `mock_getSettings` | Implement API |
| `DC-API-HARDENING` | GET | `/data-console/v1/hardening` | `data_console` | 3H | UNAVAILABLE | FIXTURE_MODE | `mock_getHardening` | Implement API |
