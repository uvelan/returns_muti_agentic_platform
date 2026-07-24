import { type EngineType } from "../contracts/browser";

export const queryKeys = {
  overview: () => ["overview"] as const,
  inventory: () => ["inventory"] as const,
  graphEvidence: {
    all: () => ["graph-evidence"] as const,
    latest: () => ["graph-evidence", "latest"] as const,
    list: (cursor?: string) => ["graph-evidence", "list", cursor ?? "first"] as const,
    document: (id: string) => ["graph-evidence", "document", id] as const,
    syncRun: (id: string) => ["graph-evidence", "syncRun", id] as const,
    report: (id: string) => ["graph-evidence", "report", id] as const,
    full: (documentId: string) => ["graph-evidence", "full", documentId] as const,
  },
  sources: {
    all: () => ["sources"] as const,
    detail: (sourceId: string) => ["sources", "detail", sourceId] as const,
  },
  browser: {
    assets: () => ["browser", "assets"] as const,
    records: (engine: EngineType, assetId: string) => ["browser", "records", engine, assetId] as const,
    record: (engine: EngineType, assetId: string, recordId: string) => ["browser", "record", engine, assetId, recordId] as const,
  },
  graphExplorer: {
    search: (id: string, depth?: number) => ["graphExplorer", "search", id, depth] as const,
    node: (nodeId: string) => ["graphExplorer", "node", nodeId] as const,
    relationship: (relationshipId: string) => ["graphExplorer", "relationship", relationshipId] as const,
    neighborhood: (nodeId: string, depth?: number) => ["graphExplorer", "neighborhood", nodeId, depth] as const,
  },
  jobs: {
    list: (type?: string, status?: string) => ["jobs", "list", type, status] as const,
    detail: (jobId: string) => ["jobs", "detail", jobId] as const,
  },
  workspaces: {
    list: () => ["workspaces", "list"] as const,
    detail: (workspaceId: string) => ["workspaces", "detail", workspaceId] as const,
    records: (workspaceId: string) => ["workspaces", "records", workspaceId] as const,
    record: (workspaceId: string, recordId: string) => ["workspaces", "record", workspaceId, recordId] as const,
  },
  scenarios: {
    list: () => ["scenarios", "list"] as const,
    detail: (scenarioId: string) => ["scenarios", "detail", scenarioId] as const,
    diffs: (scenarioId: string) => ["scenarios", "diffs", scenarioId] as const,
  }
} as const;
