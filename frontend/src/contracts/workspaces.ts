export type SandboxIssue = {
  message: string;
  field?: string | null;
};

export type SandboxRecord = {
  id: string;
  data: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  validationStatus: "VALID" | "INVALID" | "WARNING";
  issues: SandboxIssue[];
  version: number;
};

export type Workspace = {
  id: string;
  name: string;
  description: string;
  isSandbox: boolean;
  owner: string;
  createdAt: string;
  updatedAt: string;
  schemaId?: string | null;
  recordCount: number;
  version: number;
};
