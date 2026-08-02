import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  DataPreview,
  DataSourceConfiguration,
  DataSourceSchema,
  DataSourceValidation,
  DataSourceWrite,
} from "../contracts/dataSourceConfig";
import { apiClient } from "./client";

const root = "/api/v2/config/data-sources";
const keys = {
  all: ["v2", "config", "data-sources"] as const,
  detail: (sourceId: string) => [...keys.all, sourceId] as const,
  schema: (sourceId: string) => [...keys.detail(sourceId), "schema"] as const,
  data: (sourceId: string, datasetId: string) => [
    ...keys.detail(sourceId),
    "data",
    datasetId,
  ] as const,
};

export function useConfiguredDataSources() {
  return useQuery({
    queryKey: keys.all,
    queryFn: async ({ signal }) => (await apiClient<DataSourceConfiguration[]>(root, { signal })).data,
  });
}

export function useConfiguredDataSource(sourceId: string) {
  return useQuery({
    queryKey: keys.detail(sourceId),
    queryFn: async ({ signal }) => (
      await apiClient<DataSourceConfiguration>(`${root}/${encodeURIComponent(sourceId)}`, { signal })
    ).data,
    enabled: Boolean(sourceId),
  });
}

export function useCreateDataSource() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: DataSourceWrite) => (
      await apiClient<DataSourceConfiguration>(root, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    ).data,
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateDataSource(sourceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: DataSourceWrite) => (
      await apiClient<DataSourceConfiguration>(`${root}/${encodeURIComponent(sourceId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    ).data,
    onSuccess: async () => Promise.all([
      client.invalidateQueries({ queryKey: keys.all }),
      client.invalidateQueries({ queryKey: keys.detail(sourceId) }),
    ]),
  });
}

export function useRevealDataSourceCredential(sourceId: string) {
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient<{ credential: string }>(
        `${root}/${encodeURIComponent(sourceId)}/credential`,
        { cache: "no-store" },
      );
      if (!response.data) throw new Error("No saved credential was returned.");
      return response.data.credential;
    },
  });
}

export function useDeleteDataSource() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (sourceId: string) => apiClient<{ deleted: boolean }>(
      `${root}/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    ),
    onSuccess: async () => client.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useValidateDataSource(sourceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (credential?: string) => (
      await apiClient<DataSourceValidation>(
        `${root}/${encodeURIComponent(sourceId)}/validate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(credential ? { credential } : {}),
        },
      )
    ).data,
    onSuccess: async () => Promise.all([
      client.invalidateQueries({ queryKey: keys.all }),
      client.invalidateQueries({ queryKey: keys.detail(sourceId) }),
    ]),
  });
}

export function useDataSourceSchema(sourceId: string) {
  return useQuery({
    queryKey: keys.schema(sourceId),
    queryFn: async ({ signal }) => (
      await apiClient<DataSourceSchema>(`${root}/${encodeURIComponent(sourceId)}/schema`, { signal })
    ).data,
    enabled: Boolean(sourceId),
  });
}

export function useDataPreview(sourceId: string, datasetId: string) {
  return useQuery({
    queryKey: keys.data(sourceId, datasetId),
    queryFn: async ({ signal }) => (
      await apiClient<DataPreview>(
        `${root}/${encodeURIComponent(sourceId)}/data?datasetId=${encodeURIComponent(datasetId)}`,
        { signal },
      )
    ).data,
    enabled: Boolean(sourceId && datasetId),
  });
}
