import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";

export type ReleaseNode = {
  release_id: string;
  status: "DRAFT" | "VALIDATED" | "RELEASED" | "SUPERSEDED" | "ARCHIVED" | "PINNED";
  created_at: string;
  created_by: string;
  checksum_sha256: string;
  metadata?: Record<string, unknown>;
  domains?: Record<string, unknown>;
}

export type ActiveSnapshot = {
  release_id: string;
  head_revision?: number;
  checksum_sha256: string;
  loaded_at: string;
  source: "NEO4J_CONFIGURATION_GRAPH" | "VERSION_CONTROLLED_BASELINE" | "NEO4J" | "YAML_FALLBACK";
  configuration: Record<string, unknown>;
  domain_payloads: Record<string, unknown>;
}

export function useActiveSnapshot() {
  return useQuery({
    queryKey: ["console", "configuration", "active-snapshot"],
    queryFn: ({ signal }) => apiClient<ActiveSnapshot>("/data-console/v1/configuration/active-snapshot", { signal }),
    select: (res) => res.data,
    staleTime: 5_000,
  });
}

export function useConfigurationReleases() {
  return useQuery({
    queryKey: ["console", "configuration", "releases"],
    queryFn: ({ signal }) => apiClient<ReleaseNode[]>("/data-console/v1/configuration/releases", { signal }),
    select: (res) => res.data ?? [],
    staleTime: 5_000,
  });
}

export function useConfigurationReleaseDetail(releaseId: string | null) {
  return useQuery({
    queryKey: ["console", "configuration", "releases", releaseId],
    queryFn: ({ signal }) => apiClient<ReleaseNode>(`/data-console/v1/configuration/releases/${releaseId ?? ""}`, { signal }),
    select: (res) => res.data,
    enabled: Boolean(releaseId),
    staleTime: 5_000,
  });
}

export function useCreateReleaseMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ releaseId, fromActive = true }: { releaseId: string; fromActive?: boolean }) => {
      const res = await apiClient<ReleaseNode>("/data-console/v1/configuration/releases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ release_id: releaseId, from_active: fromActive }),
      });
      return res.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["console", "configuration"] });
    },
  });
}

export function useSaveDomainMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ releaseId, domainKey, payload }: { releaseId: string; domainKey: string; payload: Record<string, unknown> }) => {
      const res = await apiClient<{ domain_key: string; payload: Record<string, unknown> }>(
        `/data-console/v1/configuration/releases/${releaseId}/domains/${domainKey}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload }),
        }
      );
      return res.data;
    },
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["console", "configuration", "releases", variables.releaseId] });
      void queryClient.invalidateQueries({ queryKey: ["console", "configuration", "active-snapshot"] });
    },
  });
}

export function usePromoteReleaseMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      releaseId,
      status,
      expectedHeadRevision,
    }: {
      releaseId: string;
      status: "VALIDATED" | "RELEASED" | "ARCHIVED";
      expectedHeadRevision?: number;
    }) => {
      const res = await apiClient<ReleaseNode>(`/data-console/v1/configuration/releases/${releaseId}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, expected_head_revision: expectedHeadRevision }),
      });
      return res.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["console", "configuration"] });
    },
  });
}
