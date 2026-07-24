export type JobStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";

export type JobType = "IMPORT" | "EXPORT" | "GENERATION" | "VALIDATION" | "SYNCHRONIZATION";

export type JobMetrics = {
  totalRecords?: number;
  processedRecords?: number;
  failedRecords?: number;
  progressPercentage?: number;
};

export type JobIssue = {
  severity: "INFO" | "WARNING" | "ERROR";
  message: string;
  context?: string;
  recordIdentifier?: string;
};

export type Job = {
  id: string;
  type: JobType;
  status: JobStatus;
  target: string;
  owner: string;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  metrics: JobMetrics;
  issues?: JobIssue[];
  attempts: number;
  maxAttempts: number;
  cancellationRequestedAt?: string;
};
