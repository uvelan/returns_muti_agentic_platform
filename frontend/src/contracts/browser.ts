import { type APIResponse } from "./api";
import { type EngineType, type SourceCapability, type SourceOwnership } from "./sources";

export type { EngineType } from "./sources";

export type DisplayValueType = "NULL" | "MISSING" | "REDACTED" | "BINARY" | "STRING" | "NUMBER" | "BOOLEAN" | "DATETIME" | "OBJECT" | "ARRAY";

export type BrowserAsset = {
  assetId: string;
  sourceId: string;
  engine: EngineType;
  name: string;
  ownership: SourceOwnership;
  capability: SourceCapability;
  recordCount: number | null;
  schemaVersion: string;
};

// Base identity shared across all records
export type RecordIdentity = {
  id: string;
  assetId: string;
  engine: EngineType;
};

export type SqlRowRecord = {
  kind: "SQL_ROW";
  identity: RecordIdentity;
  data: Record<string, unknown>;
  fields: Record<string, { type: DisplayValueType; redacted: boolean }>;
};

export type MongoDocumentRecord = {
  kind: "MONGO_DOCUMENT";
  identity: RecordIdentity;
  data: Record<string, unknown>; // JSON structure
  redactedPaths: string[];
};

export type Neo4jNodeRecord = {
  kind: "NEO4J_NODE";
  identity: RecordIdentity;
  labels: string[];
  properties: Record<string, unknown>;
  propertyTypes: Record<string, { type: DisplayValueType; redacted: boolean }>;
};

export type Neo4jRelationshipRecord = {
  kind: "NEO4J_RELATIONSHIP";
  identity: RecordIdentity;
  type: string;
  startNodeId: string;
  endNodeId: string;
  properties: Record<string, unknown>;
  propertyTypes: Record<string, { type: DisplayValueType; redacted: boolean }>;
};

export type BrowserRecord = SqlRowRecord | MongoDocumentRecord | Neo4jNodeRecord | Neo4jRelationshipRecord;

export type RecordFilter = {
  field: string;
  operator: "eq" | "neq" | "gt" | "lt" | "contains";
  value: string | number | boolean | null;
};

export type RecordSort = {
  field: string;
  direction: "asc" | "desc";
};

export type BrowserAssetListResponse = APIResponse<BrowserAsset[]>;
export type BrowserRecordsResponse = APIResponse<BrowserRecord[]>;
export type BrowserRecordDetailResponse = APIResponse<BrowserRecord>;

export type DataBrowserPort = {
  getBrowserAssets(signal?: AbortSignal): Promise<BrowserAssetListResponse>;
  getRecords(engine: EngineType, assetId: string, pageCursor: string | null, pageSize: number, filters?: RecordFilter[], sort?: RecordSort, signal?: AbortSignal): Promise<BrowserRecordsResponse>;
  getRecord(engine: EngineType, assetId: string, recordId: string, signal?: AbortSignal): Promise<BrowserRecordDetailResponse>;
}

