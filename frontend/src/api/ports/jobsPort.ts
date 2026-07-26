import type { Job } from "../../contracts/jobs";
import type { APIResponse } from "../../contracts/api";

export type JobQueryPort = {
  listJobs(options?: { type?: string; status?: string; signal?: AbortSignal }): Promise<APIResponse<Job[]>>;
  getJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job>;
  cancelJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job>;
  retryJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job>;
};

export type ImportJobPort = {
  submitImport(
    payload: {
      target: string;
      format: string;
      duplicatePolicy: string;
      fieldMapping: Record<string, string>;
      content: string;
    },
    options?: { signal?: AbortSignal }
  ): Promise<Job>;
};

export type ExportJobPort = {
  submitExport(
    payload: {
      source: string;
      format: string;
      fields: string[];
    },
    options?: { signal?: AbortSignal }
  ): Promise<Job>;
};
