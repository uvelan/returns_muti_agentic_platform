/* eslint-disable @typescript-eslint/no-invalid-void-type */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createWorkspaceAdapters } from "./adapters/workspaces";
import { queryKeys } from "./queryKeyFactory";
import type { RecordMutationPayload } from "./ports/workspacesPort";
import type { SandboxRecord, Workspace } from "../contracts/workspaces";

const adapter = createWorkspaceAdapters();

export function useWorkspacesList() {
  return useQuery({
    queryKey: queryKeys.workspaces.list(),
    queryFn: ({ signal }) => adapter.listWorkspaces({ signal }),
    select: (response) => response.data ?? [],
  });
}

export function useWorkspaceDetail(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.workspaces.detail(workspaceId),
    queryFn: ({ signal }) => adapter.getWorkspace(workspaceId, { signal }),
    enabled: workspaceId.length > 0,
  });
}

export function useWorkspaceRecords(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.workspaces.records(workspaceId),
    queryFn: ({ signal }) => adapter.listRecords(workspaceId, { signal }),
    select: (response) => response.data ?? [],
    enabled: workspaceId.length > 0,
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation<Workspace, Error, { name: string; description: string; schemaId?: string }>({
    mutationFn: (payload) => adapter.createWorkspace(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.list() }),
  });
}

export function useDeleteWorkspace() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, { workspaceId: string; expectedVersion: number }>({
    mutationFn: ({ workspaceId, expectedVersion }) => adapter.deleteWorkspace(workspaceId, expectedVersion),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.list() }),
  });
}

export function useCreateWorkspaceRecord() {
  const queryClient = useQueryClient();
  return useMutation<SandboxRecord, Error, { workspaceId: string; payload: RecordMutationPayload }>({
    mutationFn: ({ workspaceId, payload }) => adapter.createRecord(workspaceId, payload),
    onSuccess: (_record, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.records(variables.workspaceId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.detail(variables.workspaceId) }),
    ]),
  });
}

export function useWorkspaceRecord(workspaceId: string, recordId: string) {
  return useQuery({
    queryKey: queryKeys.workspaces.record(workspaceId, recordId),
    queryFn: ({ signal }) => adapter.getRecord(workspaceId, recordId, { signal }),
    enabled: workspaceId.length > 0 && recordId.length > 0,
  });
}

export function useUpdateWorkspaceRecord() {
  const queryClient = useQueryClient();
  return useMutation<SandboxRecord, Error, { workspaceId: string; recordId: string; expectedVersion: number; payload: RecordMutationPayload }>({
    mutationFn: ({ workspaceId, recordId, expectedVersion, payload }) => adapter.updateRecord(workspaceId, recordId, expectedVersion, payload),
    onSuccess: (record, variables) => {
      queryClient.setQueryData(queryKeys.workspaces.record(variables.workspaceId, variables.recordId), record);
      return queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.records(variables.workspaceId) });
    },
  });
}

export function useDeleteWorkspaceRecord() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, { workspaceId: string; recordId: string; expectedVersion: number }>({
    mutationFn: ({ workspaceId, recordId, expectedVersion }) => adapter.deleteRecord(workspaceId, recordId, expectedVersion),
    onSuccess: (_result, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.records(variables.workspaceId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces.detail(variables.workspaceId) }),
    ]),
  });
}
