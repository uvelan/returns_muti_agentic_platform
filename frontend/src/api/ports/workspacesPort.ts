import type { SandboxRecord, Workspace } from "../../contracts/workspaces";
import type { APIResponse } from "../../contracts/api";

export type RecordMutationPayload = {
  data: Record<string, unknown>;
  idempotencyKey?: string;
};

export type WorkspaceMutationPort = {
  listWorkspaces(options?: { signal?: AbortSignal }): Promise<APIResponse<Workspace[]>>;
  getWorkspace(workspaceId: string, options?: { signal?: AbortSignal }): Promise<Workspace>;
  createWorkspace(payload: { name: string; description: string; schemaId?: string }, options?: { signal?: AbortSignal }): Promise<Workspace>;
  deleteWorkspace(workspaceId: string, expectedVersion: number, options?: { signal?: AbortSignal }): Promise<void>;
  listRecords(workspaceId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<SandboxRecord[]>>;
  createRecord(workspaceId: string, payload: RecordMutationPayload, options?: { signal?: AbortSignal }): Promise<SandboxRecord>;
  getRecord(workspaceId: string, recordId: string, options?: { signal?: AbortSignal }): Promise<SandboxRecord>;
  updateRecord(workspaceId: string, recordId: string, expectedVersion: number, payload: RecordMutationPayload, options?: { signal?: AbortSignal }): Promise<SandboxRecord>;
  deleteRecord(workspaceId: string, recordId: string, expectedVersion: number, options?: { signal?: AbortSignal }): Promise<void>;
};
