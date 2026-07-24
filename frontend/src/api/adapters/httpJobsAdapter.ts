import { type JobQueryPort, type ImportJobPort, type ExportJobPort } from "../ports/jobsPort";
import { type Job } from "../../contracts/jobs";
import { type APIResponse } from "../../contracts/api";
import { apiClient } from "../client";

export class HttpJobAdapter implements JobQueryPort, ImportJobPort, ExportJobPort {
  async listJobs(options?: { type?: string; status?: string; signal?: AbortSignal }): Promise<APIResponse<Job[]>> {
    const params = new URLSearchParams();
    if (options?.type) params.set("type", options.type);
    if (options?.status) params.set("status", options.status);
    const query = params.toString() ? `?${params.toString()}` : "";
    return apiClient<Job[]>(`/data-console/v1/jobs${query}`, { signal: options?.signal });
  }

  async getJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job> {
    const response = await apiClient<Job>(`/data-console/v1/jobs/${encodeURIComponent(jobId)}`, { signal: options?.signal });
    if (response.data === null) throw new Error("Unexpected null response");
    return response.data;
  }

  async cancelJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job> {
    const response = await apiClient<Job>(`/data-console/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null response");
    return response.data;
  }

  async retryJob(jobId: string, options?: { signal?: AbortSignal }): Promise<Job> {
    const response = await apiClient<Job>(`/data-console/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null response");
    return response.data;
  }

  async submitImport(
    payload: {
      target: string;
      format: string;
      duplicatePolicy: string;
      fieldMapping: Record<string, string>;
      content: string;
    },
    options?: { signal?: AbortSignal }
  ): Promise<Job> {
    const response = await apiClient<Job>("/data-console/v1/imports", {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null response");
    return response.data;
  }

  async submitExport(
    payload: { source: string; format: string; fields: string[] },
    options?: { signal?: AbortSignal }
  ): Promise<Job> {
    const response = await apiClient<Job>("/data-console/v1/exports", {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null response");
    return response.data;
  }
}
