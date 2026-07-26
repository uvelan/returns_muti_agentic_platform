export type SchemaField = {
  readonly name: string;
  readonly type: "string" | "integer" | "number" | "boolean" | "datetime" | "object" | "array";
  readonly required: boolean;
  readonly key: boolean;
  readonly description: string;
  readonly generator: string | null;
  readonly items: string | null;
};

export type DataAssetSchema = {
  readonly asset_id: string;
  readonly engine: "MONGODB" | "SQLSERVER";
  readonly database: string;
  readonly namespace: string | null;
  readonly name: string;
  readonly ownership: "SOURCE_SYSTEM" | "PLATFORM_OWNED" | "DERIVED_PROJECTION";
  readonly authoritative: boolean;
  readonly writable_in_sandbox: boolean;
  readonly description: string;
  readonly fields: readonly SchemaField[];
};

export type GraphNodeSchema = {
  readonly label: string;
  readonly key_property: string;
  readonly source_assets: readonly string[];
  readonly properties: readonly string[];
};

export type GraphRelationshipSchema = {
  readonly type: string;
  readonly from_label: string;
  readonly to_label: string;
  readonly from_key: string;
  readonly to_key: string;
};

export type SchemaRegistry = {
  readonly schema_version: "1.0";
  readonly assets: readonly DataAssetSchema[];
  readonly graph: {
    readonly nodes: readonly GraphNodeSchema[];
    readonly relationships: readonly GraphRelationshipSchema[];
  };
};

export type AIStudioProposal = {
  readonly id: string;
  readonly scenarioName: string;
  readonly mode: "DETERMINISTIC" | "AI_ASSISTED";
  readonly seed: number;
  readonly assetIds: readonly string[];
  readonly recordsPerAsset: number;
  readonly digest: string;
  readonly status: "DRAFT" | "APPLIED" | "PARTIALLY_APPLIED" | "REJECTED";
  readonly recordCounts: Readonly<Record<string, number>>;
  readonly appliedAssets: readonly string[];
  readonly blockedAssets: readonly string[];
  readonly applyErrors: Readonly<Record<string, string>>;
  readonly createdBy: string;
  readonly createdAt: string;
  readonly appliedBy: string | null;
  readonly appliedAt: string | null;
};

export type AIStudioProposalDetail = {
  readonly proposal: AIStudioProposal;
  readonly records: Readonly<Record<string, readonly Record<string, unknown>[]>>;
};

export type GraphSyncRun = {
  readonly id: string;
  readonly mode: "FULL" | "SOURCE_MONGODB" | "SQLSERVER";
  readonly status: "RUNNING" | "COMPLETED" | "FAILED";
  readonly schemaVersion: string;
  readonly sourceCounts: Readonly<Record<string, number>>;
  readonly nodeWrites: number;
  readonly relationshipWrites: number;
  readonly constraintsApplied: readonly string[];
  readonly configurationDigest: string;
  readonly errorCode: string | null;
  readonly startedBy: string;
  readonly startedAt: string;
  readonly completedAt: string | null;
};

export type FeedbackLearningRecord = {
  readonly id: string;
  readonly sessionId: string;
  readonly finalOutcome: string;
  readonly missingFieldInsights: readonly string[];
  readonly supportReworkInsights: readonly string[];
  readonly graphSyncInsights: readonly string[];
  readonly sourceUsageInsights: readonly string[];
  readonly bayAssignmentInsights: readonly string[];
  readonly recommendations: readonly string[];
  readonly evidenceDigest: string;
  readonly reviewStatus: string;
  readonly createdAt: string;
};
