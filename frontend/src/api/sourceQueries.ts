import { useQuery } from "@tanstack/react-query";
import { createDataSourcesPort } from "./adapters/sources";
import { queryKeys } from "./queryKeyFactory";

const port = createDataSourcesPort();

export function useSources() {
  return useQuery({
    queryKey: queryKeys.sources.all(),
    queryFn: async ({ signal }) => {
      const response = await port.getSources(signal);
      return response.data;
    },
    retry: 2,
  });
}

export function useSourceDetail(sourceId: string) {
  return useQuery({
    queryKey: queryKeys.sources.detail(sourceId),
    queryFn: async ({ signal }) => {
      const response = await port.getSource(sourceId, signal);
      return response.data;
    },
    enabled: sourceId.length > 0,
    retry: 2,
  });
}
