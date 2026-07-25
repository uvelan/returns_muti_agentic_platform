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

export type SourceAssetSummary = {
  assetId: string;
  name: string;
  kind: "COLLECTION" | "TABLE";
  ownership: "SOURCE_SYSTEM" | "PLATFORM_OWNED" | "DERIVED_PROJECTION";
  authoritative: boolean;
  writableInSandbox: boolean;
};

export type SourceDetail = SourceItem & {
  connectionIdentity: string;
  inventoryTotals: {
    assets: number;
    records: number | null;
  };
  lastMetadataRefresh: string | null;
  dependencyWarnings: string[];
  assets: SourceAssetSummary[];
};

export type SourceListResponse = APIResponse<SourceItem[]>;
export type SourceDetailResponse = APIResponse<SourceDetail>;

export type DataSourcesPort = {
  getSources(signal?: AbortSignal): Promise<SourceListResponse>;
  getSource(sourceId: string, signal?: AbortSignal): Promise<SourceDetailResponse>;
};
