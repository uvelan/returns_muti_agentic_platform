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
| `DC-API-WORKSPACES-LIST` | GET | `/data-console/v1/workspaces` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_getWorkspaces` | Implement API |
| `DC-API-WORKSPACES-CREATE` | POST | `/data-console/v1/workspaces` | `data_console` | 3F | UNAVAILABLE | FIXTURE_MODE | `mock_createWorkspace` | Implement POST |
| `DC-API-IMPORTS-LIST` | GET | `/data-console/v1/imports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getImports` | Implement API |
| `DC-API-EXPORTS-LIST` | GET | `/data-console/v1/exports` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getExports` | Implement API |
| `DC-API-JOBS-LIST` | GET | `/data-console/v1/jobs` | `data_console` | 3E | UNAVAILABLE | FIXTURE_MODE | `mock_getJobs` | Implement API |
| `DC-API-SCENARIOS-LIST` | GET | `/data-console/v1/scenarios` | `data_console` | 3G | UNAVAILABLE | FIXTURE_MODE | `mock_getScenarios` | Implement API |
