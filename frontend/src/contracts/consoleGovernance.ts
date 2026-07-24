export type AuditLog = {
  id: string;
  action: string;
  actor: string;
  target: string;
  timestamp: string;
  details: Record<string, unknown>;
};

export type GovernanceSummary = {
  status: "VALIDATED" | "DEGRADED";
  evaluatedAt: string;
  catalogVersion: string;
  catalogDigest: string;
  catalogPath: string;
  assetCount: number;
  authoritativeAssetCount: number;
  sampledAssetCount: number;
  ownershipCounts: Record<string, number>;
  violations: string[];
};

export type ConsoleSettings = {
  environment: string;
  retentionDays: number;
  strictMode: boolean;
  eventStreamRetention: number;
  aiProviderOrder: string[];
  aiInterceptionEnabled: boolean;
  seedVersion: string;
};

export type HardeningCheck = {
  id: string;
  status: "PASS" | "WARN" | "FAIL" | "NOT_VALIDATED";
  evidence: string;
  details: string;
};

export type HardeningSummary = {
  status: "PASS" | "DEGRADED" | "FAIL" | "NOT_VALIDATED";
  evaluatedAt: string;
  score: number | null;
  vulnerabilities: number | null;
  checks: HardeningCheck[];
};
