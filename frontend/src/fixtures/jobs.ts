import type { Job } from "../contracts/jobs";

const MOCK_JOBS: Job[] = [
  {
    id: "job-imp-1",
    type: "IMPORT",
    status: "COMPLETED",
    target: "Customer (Sales)",
    owner: "alice@example.com",
    createdAt: "2026-07-22T08:00:00Z",
    startedAt: "2026-07-22T08:01:00Z",
    completedAt: "2026-07-22T08:05:00Z",
    attempts: 1,
    maxAttempts: 3,
    metrics: {
      totalRecords: 1000,
      processedRecords: 1000,
      failedRecords: 0,
      progressPercentage: 100
    }
  },
  {
    id: "job-exp-1",
    type: "EXPORT",
    status: "RUNNING",
    target: "Transactions (Q2)",
    owner: "bob@example.com",
    createdAt: "2026-07-23T10:00:00Z",
    startedAt: "2026-07-23T10:02:00Z",
    attempts: 1,
    maxAttempts: 3,
    metrics: {
      totalRecords: 50000,
      processedRecords: 25000,
      failedRecords: 0,
      progressPercentage: 50
    }
  },
  {
    id: "job-gen-1",
    type: "GENERATION",
    status: "FAILED",
    target: "Scenario: Edge Cases",
    owner: "system",
    createdAt: "2026-07-21T12:00:00Z",
    startedAt: "2026-07-21T12:01:00Z",
    completedAt: "2026-07-21T12:02:00Z",
    attempts: 1,
    maxAttempts: 3,
    metrics: {
      totalRecords: 50,
      processedRecords: 10,
      failedRecords: 40,
      progressPercentage: 20
    },
    issues: [
      {
        severity: "ERROR",
        message: "Model timed out during generation.",
        context: "Batch 2"
      }
    ]
  }
];

export function getJobsFixture(typeFilter?: string): Job[] {
  if (typeFilter) {
    return MOCK_JOBS.filter(job => job.type === typeFilter);
  }
  return MOCK_JOBS;
}

export function getJobFixture(id: string): Job {
  const job = MOCK_JOBS.find(j => j.id === id);
  if (!job) {
    throw new Error(`Job not found: ${id}`);
  }
  return job;
}
