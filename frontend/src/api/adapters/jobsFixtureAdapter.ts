import type { JobQueryPort, ImportJobPort, ExportJobPort } from "../ports/jobsPort";
import type { Job } from "../../contracts/jobs";
import type { APIResponse } from "../../contracts/api";
import { getJobsFixture, getJobFixture } from "../../fixtures/jobs";

export function createFixtureJobAdapters(): JobQueryPort & ImportJobPort & ExportJobPort {
  return {
    async listJobs(options?: { type?: string; status?: string; signal?: AbortSignal }): Promise<APIResponse<Job[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const jobs = getJobsFixture(options?.type);
      return {
        data: jobs,
        meta: {
          schema_version: "1.0",
          request_id: "req-mock-1",
          generated_at: new Date().toISOString(),
          freshness: "LIVE",
          partial: false,
          warnings: []
        },
        page: null
      };
    },

    async getJob(jobId: string): Promise<Job> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return getJobFixture(jobId);
    },

    async cancelJob(jobId: string): Promise<Job> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return getJobFixture(jobId);
    },

    async retryJob(jobId: string): Promise<Job> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return getJobFixture(jobId);
    },

    async submitImport(payload: { target: string; format: string; duplicatePolicy: string; fieldMapping: Record<string, string> }): Promise<Job> {
      await new Promise(resolve => setTimeout(resolve, 500));
      const id = String(Date.now());
      return {
        id: `job-imp-mock-${id}`,
        type: "IMPORT",
        status: "PENDING",
        target: payload.target,
        owner: "currentUser@example.com",
        createdAt: new Date().toISOString(),
        attempts: 1,
        maxAttempts: 3,
        metrics: {
          progressPercentage: 0
        }
      };
    },

    async submitExport(payload: { source: string; format: string; fields: string[] }): Promise<Job> {
      await new Promise(resolve => setTimeout(resolve, 500));
      const id = String(Date.now());
      return {
        id: `job-exp-mock-${id}`,
        type: "EXPORT",
        status: "PENDING",
        target: payload.source,
        owner: "currentUser@example.com",
        createdAt: new Date().toISOString(),
        attempts: 1,
        maxAttempts: 3,
        metrics: {
          progressPercentage: 0
        }
      };
    }
  };
}
