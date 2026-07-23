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
} as const;
