export type SQLColumn = {
  readonly column_id: number;
  readonly name: string;
  readonly data_type: {
    readonly schema_name: string;
    readonly name: string;
  };
  readonly is_nullable: boolean;
  readonly is_identity: boolean;
  readonly is_computed: boolean;
};

export type SQLTable = {
  readonly object_id: number;
  readonly name: string;
  readonly approximate_row_count: number;
  readonly columns: readonly SQLColumn[];
};

export type SQLSchema = {
  readonly schema_id: number;
  readonly name: string;
  readonly tables: readonly SQLTable[];
  readonly views: readonly SQLTable[];
};

export type SQLServerInventory = {
  readonly database_name: string;
  readonly observed_at: string;
  readonly schemas: readonly SQLSchema[];
};

export type MongoCollection = {
  readonly name: string;
  readonly approximate_document_count: number;
  readonly indexes: readonly {
    readonly name: string;
    readonly is_unique: boolean;
  }[];
};

export type MongoInventory = {
  readonly database_name: string;
  readonly observed_at: string;
  readonly collections: readonly MongoCollection[];
};

export type Neo4jInventory = {
  readonly labels: readonly string[];
  readonly relationship_types: readonly string[];
};

export type UnifiedInventory = {
  readonly sqlserver: SQLServerInventory | null;
  readonly mongodb: MongoInventory | null;
  readonly neo4j: Neo4jInventory | null;
};

export type InventoryDetail = {
  readonly assetId: string;
  readonly engine: string;
  readonly name: string;
  readonly ownership: string;
  readonly capability: string;
  readonly recordCount: number | null;
  readonly schemaVersion: string;
  readonly operations: readonly string[];
  readonly metadata: Record<string, unknown>;
};
