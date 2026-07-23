import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "./queryKeyFactory";
import { createDataSourcesPort } from "./adapters/sources";

const port = createDataSourcesPort();

export function useSources() {
  return useQuery({
    queryKey: queryKeys.sources.all(),
    queryFn: async ({ signal }) => {
      const response = await port.getSources(signal);
      return response.data;
    },
    retry: (failureCount, error) => {
      // Do not retry capability errors or fixture errors
      if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
        return false;
      }
      return failureCount < 3;
    },
  });
}

export function useSourceDetail(sourceId: string) {
  return useQuery({
    queryKey: queryKeys.sources.detail(sourceId),
    queryFn: async ({ signal }) => {
      const response = await port.getSource(sourceId, signal);
      return response.data;
    },
    enabled: !!sourceId,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
        return false;
      }
      return failureCount < 3;
    },
  });
}
