import { type APIResponse } from "./api";

export type EngineType = "SQL_SERVER" | "MONGODB" | "NEO4J" | "PLATFORM";
export type SourceEnvironment = "PRODUCTION" | "STAGING" | "SANDBOX" | "LOCAL";
export type SourceOwnership = "AUTHORITATIVE" | "DERIVED" | "SYNTHETIC" | "INTERNAL";
export type SourceCapability = "READ_ONLY" | "WRITABLE" | "UNKNOWN";
export type SourceHealth = "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "UNKNOWN";

export type SourceItem = {
  id: string;
  name: string;
  engine: EngineType;
  environment: SourceEnvironment;
  ownership: SourceOwnership;
  health: SourceHealth;
  capability: SourceCapability;
  lastInventoryTime: string | null;
};

export type SourceDetail = SourceItem & {
  connectionIdentity: string; // Safe connection identity, no DSN/credentials
  inventoryTotals: {
    assets: number;
    records: number;
  };
  lastMetadataRefresh: string | null;
  dependencyWarnings: string[];
};

export type SourceListResponse = APIResponse<SourceItem[]>;
export type SourceDetailResponse = APIResponse<SourceDetail>;

export type DataSourcesPort = {
  getSources(signal?: AbortSignal): Promise<SourceListResponse>;
  getSource(sourceId: string, signal?: AbortSignal): Promise<SourceDetailResponse>;
}
