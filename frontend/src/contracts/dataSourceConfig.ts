export type DataSourceType = "MONGODB" | "SQLSERVER" | "NEO4J";
export type DataSourceStatus = "VALID" | "INVALID" | "NOT_VALIDATED" | "VALIDATING";

export type DataSourceWrite = {
  name: string;
  description: string;
  sourceType: DataSourceType;
  accessMode: "READ_ONLY" | "READ_WRITE";
  host: string | null;
  port: number | null;
  uri: string | null;
  database: string;
  username: string | null;
  credentialVaultReference: string;
  credentialKind: "DSN" | "PASSWORD";
  credential?: string;
  sslEnabled: boolean;
};

export type DataSourceConfiguration = DataSourceWrite & {
  id: string;
  status: DataSourceStatus;
  managed: boolean;
  createdAt: string;
  updatedAt: string;
  lastValidatedAt: string | null;
  validationMessage: string | null;
};

export type ValidationCheck = {
  name: string;
  status: "PASSED" | "FAILED";
  message: string;
};

export type DataSourceValidation = {
  sourceId: string;
  status: "VALID" | "INVALID";
  checkedAt: string;
  latencyMs: number | null;
  checks: ValidationCheck[];
};

export type SchemaField = {
  name: string;
  type: string;
  required: boolean;
  key: boolean;
  description: string;
};

export type SchemaDataset = {
  id: string;
  name: string;
  namespace: string | null;
  kind: "TABLE" | "COLLECTION" | "NODE";
  description: string;
  fields: SchemaField[];
};

export type DataSourceSchema = {
  sourceId: string;
  schemaVersion: string;
  datasets: SchemaDataset[];
};

export type DataPreview = {
  sourceId: string;
  datasetId: string;
  columns: string[];
  rows: Record<string, unknown>[];
  redacted: boolean;
};
