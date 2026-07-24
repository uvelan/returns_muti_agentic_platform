export type Scenario = {
  id: string;
  name: string;
  description: string;
  baseWorkspaceId: string;
  status: "DRAFT" | "GENERATING" | "VALIDATING" | "READY" | "APPROVED" | "FAILED" | "ARCHIVED";
  parameters: Record<string, unknown>;
  createdAt: string;
  owner: string;
  version: number;
  generatedDigest?: string | null;
  validatedDigest?: string | null;
  validationIssues: string[];
};

export type ScenarioDiff = {
  recordId: string;
  status: "ADDED" | "REMOVED" | "MODIFIED";
  baseData?: Record<string, unknown>;
  scenarioData?: Record<string, unknown>;
  issues?: string[];
};

export type ScenarioPreviewRecord = { recordId: string; data: Record<string, unknown>; issues: string[] };
