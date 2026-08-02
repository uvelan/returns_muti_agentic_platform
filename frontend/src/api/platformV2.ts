import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  ConfigurationModule,
  ImportRecord,
  ReleaseManifest,
  ReleaseStatus,
  SchemaDesignContext,
  SyncResult,
  ValidationResult,
} from "../contracts/platformV2";
import { apiClient } from "./client";

const root = "/api/v2";
const jsonHeaders = { "Content-Type": "application/json" };
const keys = {
  modules: ["v2", "configuration", "modules"] as const,
  releases: ["v2", "configuration", "releases"] as const,
  schemaDesign: (id: string) => ["v2", "schema-design", id] as const,
};

export function useConfigurationModules() {
  return useQuery({
    queryKey: keys.modules,
    queryFn: async ({ signal }) => (
      await apiClient<ConfigurationModule[]>(`${root}/configuration/modules`, { signal })
    ).data,
  });
}

export function useCreateModuleDraft() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ moduleId, fromVersion, configurationVersion }: {
      moduleId: string;
      fromVersion: string;
      configurationVersion: string;
    }) => (
      await apiClient<ConfigurationModule>(
        `${root}/configuration/modules/${encodeURIComponent(moduleId)}/drafts`,
        { method: "POST", headers: jsonHeaders, body: JSON.stringify({ fromVersion, configurationVersion }) },
      )
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.modules }),
  });
}

export function useModuleAction(action: "validate" | "submit" | "approve") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ moduleId, version }: { moduleId: string; version: string }) => (
      await apiClient<ConfigurationModule | ValidationResult>(
        `${root}/configuration/modules/${encodeURIComponent(moduleId)}/drafts/${encodeURIComponent(version)}/${action}`,
        { method: "POST" },
      )
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.modules }),
  });
}

export function useReleases() {
  return useQuery({
    queryKey: keys.releases,
    queryFn: async ({ signal }) => (
      await apiClient<ReleaseManifest[]>(`${root}/configuration/releases`, { signal })
    ).data,
  });
}

export function useCreateRelease() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { releaseId: string; modules: { moduleId: string; version: string; checksum: string }[] }) => (
      await apiClient<ReleaseManifest>(`${root}/configuration/releases`, {
        method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
      })
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.releases }),
  });
}

export function useReleaseAction(action: "resolve" | "validate" | "activate" | "transition") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ releaseId, status }: { releaseId: string; status?: ReleaseStatus }) => (
      await apiClient<ReleaseManifest>(
        `${root}/configuration/releases/${encodeURIComponent(releaseId)}/${action}`,
        {
          method: "POST",
          headers: status ? jsonHeaders : undefined,
          body: status ? JSON.stringify({ status }) : undefined,
        },
      )
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.releases }),
  });
}

export function useCreateSchemaDesign() {
  return useMutation({
    mutationFn: async (body: {
      selectedModules: string[];
      requestedCapabilities: string[];
      sourceStructures: unknown[];
      existingSchema?: Record<string, unknown>;
    }) => (
      await apiClient<SchemaDesignContext>(`${root}/schema-design/requests`, {
        method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
      })
    ).data,
  });
}

export function useSchemaDesignAction(requestId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ action, body }: { action: "next-question" | "answers"; body?: unknown }) => (
      await apiClient<SchemaDesignContext>(
        `${root}/schema-design/requests/${encodeURIComponent(requestId)}/${action}`,
        { method: "POST", headers: body ? jsonHeaders : undefined, body: body ? JSON.stringify(body) : undefined },
      )
    ).data,
    onSuccess: (value) => client.setQueryData(keys.schemaDesign(requestId), value),
  });
}

export function useImportConfiguration() {
  return useMutation({
    mutationFn: async (body: { format: "JSON" | "YAML"; content: string }) => (
      await apiClient<ImportRecord>(`${root}/configuration/imports`, {
        method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
      })
    ).data,
  });
}

export function useCreateImportDrafts() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (importId: string) => (
      await apiClient<ImportRecord>(
        `${root}/configuration/imports/${encodeURIComponent(importId)}/create-drafts`,
        { method: "POST" },
      )
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.modules }),
  });
}

export function useOrderSync() {
  return useMutation({
    mutationFn: async (body: {
      mode: "partial" | "full";
      releaseId: string;
      fullOrderId?: string;
      anchorType?: string;
      anchorValue?: string;
    }) => {
      const common = {
        releaseId: body.releaseId,
        authorizationScope: { accounts: [], branches: [], maxCandidates: 20 },
        idempotencyKey: `${body.mode}-${crypto.randomUUID()}`,
      };
      const payload = body.mode === "full"
        ? { ...common, fullOrderId: body.fullOrderId }
        : { ...common, anchor: { type: body.anchorType, value: body.anchorValue } };
      return (
        await apiClient<SyncResult>(`${root}/order-sync/${body.mode}`, {
          method: "POST", headers: jsonHeaders, body: JSON.stringify(payload),
        })
      ).data;
    },
  });
}
