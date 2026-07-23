/* eslint-disable */
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "./queryKeyFactory";
import { createDataBrowserPort } from "./adapters/browser";
import { type EngineType, type RecordFilter, type RecordSort } from "../contracts/browser";

const port = createDataBrowserPort();

export function useBrowserAssets() {
  return useQuery({
    queryKey: queryKeys.browser.assets(),
    queryFn: async ({ signal }) => {
      const response = await port.getBrowserAssets(signal);
      return response.data;
    },
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
        return false;
      }
      return failureCount < 3;
    },
  });
}

export function useBrowserRecords(engine: EngineType, assetId: string, pageCursor: string | null, pageSize: number, filters?: RecordFilter[], sort?: RecordSort) {
  return useQuery({
    // We add pagination/filter/sort into the query key so they cache uniquely
    queryKey: [...queryKeys.browser.records(engine, assetId), { pageCursor, pageSize, filters, sort }],
    queryFn: async ({ signal }) => {
      const response = await port.getRecords(engine, assetId, pageCursor, pageSize, filters, sort, signal);
      return response;
    },
    enabled: !!engine && !!assetId,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
        return false;
      }
      return failureCount < 3;
    },
  });
}

export function useRecordDetail(engine: EngineType, assetId: string, recordId: string) {
  return useQuery({
    queryKey: queryKeys.browser.record(engine, assetId, recordId),
    queryFn: async ({ signal }) => {
      const response = await port.getRecord(engine, assetId, recordId, signal);
      return response.data;
    },
    enabled: !!engine && !!assetId && !!recordId,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
        return false;
      }
      return failureCount < 3;
    },
  });
}

