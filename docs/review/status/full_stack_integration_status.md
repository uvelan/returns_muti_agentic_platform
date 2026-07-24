# Full Stack Integration Status

This document tracks the closure of routes, APIs, capabilities, and testing for the Complete Frontend and Backend Integration phases.

## Route and API Closure Checklist

| Frontend Route | API Port / Live Adapter | Backend Operation | Persistence Owner | Tests | Current Capability |
|---|---|---|---|---|---|
| `/overview` | `getOverview` | `GET /data-console/v1/overview` | Platform | `CONTRACT_TESTED` | LIVE |
| `/data-console/inventory` | `getInventory` | `GET /data-console/v1/inventory` | Multi | `CONTRACT_TESTED` | LIVE |
| `/data-console/inventory/:engine/:assetId` | `getInventoryAsset` | `GET /data-console/v1/inventory/{engine}/{assetId}` | Multi | `PENDING` | FIXTURE |
| `/data-console/graph-evidence` | `getGraphEvidence` | `GET /data-console/v1/graph-evidence` | Neo4j | `SANDBOX_VALIDATED` | LIVE |
| `/data-console/sources` | `getSources` | `GET /data-console/v1/sources` | Multi | `PENDING` | FIXTURE |
| `/data-console/sources/:sourceId` | `getSource` | `GET /data-console/v1/sources/{sourceId}` | Multi | `PENDING` | FIXTURE |
| `/data-console/browser` | `getBrowserAssets` | `GET /data-console/v1/browser/assets` | Multi | `PENDING` | FIXTURE |
| `/data-console/browser/:engine/:assetId` | `getRecords` | `GET /data-console/v1/browser/{engine}/{assetId}/records` | Multi | `PENDING` | FIXTURE |
| `/data-console/browser/:engine/:assetId/records/:recordId` | `getRecord` | `GET /data-console/v1/browser/{engine}/{assetId}/records/{recordId}` | Multi | `PENDING` | FIXTURE |
| `/data-console/workspaces` | `getWorkspaces` | `GET /data-console/v1/workspaces` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/workspaces/new` | `createWorkspace` | `POST /data-console/v1/workspaces` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/workspaces/:workspaceId` | `getWorkspace` | `GET /data-console/v1/workspaces/{workspaceId}` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/workspaces/:workspaceId/new` | `createWorkspaceRecord` | `POST /data-console/v1/workspaces/{workspaceId}/records` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/workspaces/:workspaceId/records/:recordId/edit` | `getWorkspaceRecord` / `updateWorkspaceRecord` | `GET / PATCH /data-console/v1/workspaces/{workspaceId}/records/{recordId}` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/graph` | `searchGraph` | `GET /data-console/v1/graph/search` | Neo4j | `PENDING` | FIXTURE |
| `/data-console/graph/nodes/:nodeId` | `getNode` | `GET /data-console/v1/graph/nodes/{nodeId}` | Neo4j | `PENDING` | FIXTURE |
| `/data-console/graph/relationships/:relationshipId` | `getRelationship` | `GET /data-console/v1/graph/relationships/{relationshipId}` | Neo4j | `PENDING` | FIXTURE |
| `/data-console/imports` | `getImports` | `GET /data-console/v1/imports` | Platform | `PENDING` | FIXTURE |
| `/data-console/imports/new` | `createImport` | `POST /data-console/v1/imports` | Platform | `PENDING` | FIXTURE |
| `/data-console/imports/:jobId` | `getImport` | `GET /data-console/v1/imports/{jobId}` | Platform | `PENDING` | FIXTURE |
| `/data-console/exports` | `getExports` | `GET /data-console/v1/exports` | Platform | `PENDING` | FIXTURE |
| `/data-console/exports/new` | `createExport` | `POST /data-console/v1/exports` | Platform | `PENDING` | FIXTURE |
| `/data-console/exports/:jobId` | `getExport` | `GET /data-console/v1/exports/{jobId}` | Platform | `PENDING` | FIXTURE |
| `/data-console/jobs` | `getJobs` | `GET /data-console/v1/jobs` | Platform | `PENDING` | FIXTURE |
| `/data-console/jobs/:jobId` | `getJob` | `GET /data-console/v1/jobs/{jobId}` | Platform | `PENDING` | FIXTURE |
| `/data-console/scenarios` | `getScenarios` | `GET /data-console/v1/scenarios` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/scenarios/new` | `createScenario` | `POST /data-console/v1/scenarios` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/scenarios/:scenarioId` | `getScenario` | `GET /data-console/v1/scenarios/{scenarioId}` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/scenarios/:scenarioId/preview` | `previewScenario` | `GET /data-console/v1/scenarios/{scenarioId}/preview` | Sandbox | `PENDING` | FIXTURE |
| `/data-console/audit` | `getAuditList` | `GET /data-console/v1/audit` | Platform | `PENDING` | FIXTURE |
| `/data-console/governance` | `getGovernance` | `GET /data-console/v1/governance` | Platform | `PENDING` | FIXTURE |
| `/data-console/settings` | `getSettings` | `GET /data-console/v1/settings` | Platform | `PENDING` | FIXTURE |
| `/data-console/hardening` | `getHardening` | `GET /data-console/v1/hardening` | Platform | `PENDING` | FIXTURE |

*(Routes and API ports are currently being reconciled and connected. This table will be updated continuously as backend implementation and live integration progress.)*
