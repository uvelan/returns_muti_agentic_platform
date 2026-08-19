export type SourceEngine = "MONGODB" | "POSTGRESQL" | "SQLSERVER" | "NEO4J";

export type SourceStatus =
  | "NOT_VALIDATED"
  | "CONNECTED"
  | "VALIDATION_FAILED"
  | "AUTHENTICATION_FAILED"
  | "UNREACHABLE";

export type SourceObjectKind =
  | "database"
  | "schema"
  | "table"
  | "collection"
  | "entity"
  | "relationship"
  | "field";

export type SourceObject = {
  readonly id: string;
  readonly name: string;
  readonly kind: SourceObjectKind;
  readonly path: readonly string[];
  readonly selectable: boolean;
  readonly children: readonly SourceObject[];
  readonly fields?: readonly SourceField[];
  readonly estimatedRows?: number | null;
};

export type SourceField = {
  readonly name: string;
  readonly dataType: string;
  readonly nullable: boolean;
  readonly identifier: boolean;
  readonly indexed: boolean;
};

export type AnalyzerSource = {
  readonly id: string;
  readonly name: string;
  readonly engine: SourceEngine;
  readonly status: SourceStatus;
  readonly host: string;
  readonly port: number;
  readonly database: string;
  readonly username: string | null;
  readonly lastValidatedAt: string | null;
  readonly objectCount: number;
  readonly objects: readonly SourceObject[];
};

export type SourceInput = {
  readonly name: string;
  readonly engine: SourceEngine;
  readonly host: string;
  readonly port: number;
  readonly database: string;
  readonly username: string;
  readonly password?: string;
};

/** One node of a bounded, read-only sample read from an external graph source. */
export type PreviewGraphNode = {
  readonly id: string;
  readonly labels: readonly string[];
  readonly properties: Readonly<Record<string, unknown>>;
};

/** One relationship in that same sample. Both endpoints are always present in `nodes`. */
export type PreviewGraphEdge = {
  readonly id: string;
  readonly type: string;
  readonly fromId: string;
  readonly toId: string;
  readonly properties: Readonly<Record<string, unknown>>;
};

export type PreviewGraph = {
  readonly nodes: readonly PreviewGraphNode[];
  readonly edges: readonly PreviewGraphEdge[];
};

export type PreviewPage = {
  readonly columns: readonly string[];
  readonly rows: readonly Readonly<Record<string, unknown>>[];
  readonly page: number;
  readonly pageSize: number;
  readonly total: number | null;
  /**
   * Present only for external graph sources. `null` everywhere else, which is
   * what the Graph tab renders its empty state from -- it never invents nodes.
   */
  readonly graph: PreviewGraph | null;
};

export type AnalysisStage =
  | "PREPARING"
  | "READING_METADATA"
  | "EVALUATING_IDENTIFIERS"
  | "DISCOVERING_ENTITIES"
  | "EVALUATING_RELATIONSHIPS"
  | "BUILDING_PROPOSAL"
  | "COMPARING_EXISTING"
  | "REVIEWING_INDEXES"
  | "VALIDATING"
  | "COMPLETE"
  /** The proposal was built from declared source metadata; no model was reachable. */
  | "COMPLETE_WITHOUT_MODEL"
  | "FAILED";

export type AnalysisRun = {
  readonly id: string;
  readonly status: "RUNNING" | "COMPLETED" | "PARTIALLY_COMPLETED" | "FAILED";
  readonly stage: AnalysisStage;
  readonly selectedObjectIds: readonly string[];
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly warningCount: number;
};

export type GraphProperty = {
  readonly id: string;
  readonly name: string;
  readonly dataType: string;
  readonly required: boolean;
  readonly identifier: boolean;
  readonly indexed: boolean;
  readonly sourceObjectId: string | null;
  readonly sourceField: string | null;
};

export type GraphEntity = {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly x: number;
  readonly y: number;
  readonly properties: readonly GraphProperty[];
  readonly constraints: readonly string[];
  readonly change: "UNCHANGED" | "ADDED" | "CHANGED" | "REMOVED";
};

export type GraphRelationship = {
  readonly id: string;
  readonly name: string;
  readonly fromEntityId: string;
  readonly toEntityId: string;
  readonly direction: "OUTBOUND" | "INBOUND";
  readonly properties: readonly GraphProperty[];
  readonly sourceObjectId: string | null;
  readonly change: "UNCHANGED" | "ADDED" | "CHANGED" | "REMOVED";
};

export type GraphSchema = {
  readonly id: string;
  readonly version: number;
  readonly status: "DRAFT" | "VALIDATION_REQUIRED" | "READY" | "FINALIZED";
  readonly updatedAt: string;
  readonly entities: readonly GraphEntity[];
  readonly relationships: readonly GraphRelationship[];
};

export type ValidationIssue = {
  readonly id: string;
  readonly severity: "WARNING" | "BLOCKING";
  readonly code: string;
  readonly message: string;
  readonly objectId: string;
};

export type SchemaValidation = {
  readonly status: "VALID" | "WARNING" | "BLOCKING";
  readonly checkedAt: string;
  readonly issues: readonly ValidationIssue[];
};

export type AgentContext = {
  readonly workspace: "ANALYZER" | "SCHEMA" | "SYNC";
  readonly selectedSourceId?: string;
  readonly selectedObjectId?: string;
  readonly selectedGraphObjectId?: string;
  readonly selectedScope?: readonly string[];
  readonly syncRunId?: string;
};

export type AgentMessage = {
  readonly id: string;
  readonly role: "USER" | "AGENT";
  readonly content: string;
  readonly createdAt: string;
};

export type AgentRecommendation = {
  readonly id: string;
  readonly summary: string;
  readonly rationale: string;
  readonly target: "SYSTEM_GRAPH";
  readonly status: "PENDING" | "APPLIED" | "REJECTED";
  readonly operations: readonly Readonly<Record<string, unknown>>[];
};

export type AgentReply = {
  readonly message: AgentMessage;
  readonly recommendation: AgentRecommendation | null;
};

export type SyncMode = "FULL" | "PARTIAL";
export type SyncStatus = "PREPARING" | "RUNNING" | "COMPLETED" | "PARTIALLY_COMPLETED" | "FAILED" | "CANCELLED";

export type SyncRun = {
  readonly id: string;
  readonly mode: SyncMode;
  readonly status: SyncStatus;
  readonly scope: readonly string[];
  readonly currentSource: string | null;
  readonly currentObject: string | null;
  readonly currentActivity: string;
  readonly itemsRead: number;
  readonly itemsProcessed: number;
  readonly nodesWritten: number;
  readonly relationshipsWritten: number;
  readonly failedItems: number;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly error: string | null;
};

export type AnalyzerBootstrap = {
  readonly sources: readonly AnalyzerSource[];
  readonly existingSchema: GraphSchema | null;
  readonly proposedSchema: GraphSchema | null;
  readonly validation: SchemaValidation | null;
  readonly activeAnalysis: AnalysisRun | null;
  readonly activeSync: SyncRun | null;
  readonly syncHistory: readonly SyncRun[];
};
