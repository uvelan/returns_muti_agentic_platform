import { apiClient } from "../client";
import type { APIResponse } from "../../contracts/api";
import type { SandboxRecord, Workspace } from "../../contracts/workspaces";
import type { RecordMutationPayload, WorkspaceMutationPort } from "../ports/workspacesPort";

export class HttpWorkspaceAdapter implements WorkspaceMutationPort {
  async listWorkspaces(options?: { signal?: AbortSignal }): Promise<APIResponse<Workspace[]>> {
    return apiClient<Workspace[]>("/data-console/v1/workspaces", { signal: options?.signal });
  }

  async getWorkspace(workspaceId: string, options?: { signal?: AbortSignal }): Promise<Workspace> {
    const response = await apiClient<Workspace>(`/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}`, { signal: options?.signal });
    if (response.data === null) throw new Error("Unexpected null workspace response");
    return response.data;
  }

  async createWorkspace(payload: { name: string; description: string; schemaId?: string }, options?: { signal?: AbortSignal }): Promise<Workspace> {
    const response = await apiClient<Workspace>("/data-console/v1/workspaces", {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null workspace response");
    return response.data;
  }

  async deleteWorkspace(workspaceId: string, expectedVersion: number, options?: { signal?: AbortSignal }): Promise<void> {
    await apiClient(`/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}?expectedVersion=${String(expectedVersion)}`, {
      method: "DELETE",
      signal: options?.signal,
    });
  }

  async listRecords(workspaceId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<SandboxRecord[]>> {
    return apiClient<SandboxRecord[]>(`/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}/records`, { signal: options?.signal });
  }

  async createRecord(workspaceId: string, payload: RecordMutationPayload, options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
    const response = await apiClient<SandboxRecord>(`/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}/records`, {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    if (response.data === null) throw new Error("Unexpected null record response");
    return response.data;
  }

  async getRecord(workspaceId: string, recordId: string, options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
    const response = await apiClient<SandboxRecord>(`/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}/records/${encodeURIComponent(recordId)}`, { signal: options?.signal });
    if (response.data === null) throw new Error("Unexpected null record response");
    return response.data;
  }

  async updateRecord(workspaceId: string, recordId: string, expectedVersion: number, payload: RecordMutationPayload, options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
    const response = await apiClient<SandboxRecord>(
      `/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}/records/${encodeURIComponent(recordId)}?expectedVersion=${String(expectedVersion)}`,
      { method: "PATCH", body: JSON.stringify(payload), signal: options?.signal },
    );
    if (response.data === null) throw new Error("Unexpected null record response");
    return response.data;
  }

  async deleteRecord(workspaceId: string, recordId: string, expectedVersion: number, options?: { signal?: AbortSignal }): Promise<void> {
    await apiClient(
      `/data-console/v1/workspaces/${encodeURIComponent(workspaceId)}/records/${encodeURIComponent(recordId)}?expectedVersion=${String(expectedVersion)}`,
      { method: "DELETE", signal: options?.signal },
    );
  }
}
