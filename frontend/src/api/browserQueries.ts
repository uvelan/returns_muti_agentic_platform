import { useQuery } from "@tanstack/react-query";
import { type EngineType, type RecordFilter, type RecordSort } from "../contracts/browser";
import { createDataBrowserPort } from "./adapters/browser";
import { queryKeys } from "./queryKeyFactory";

const port = createDataBrowserPort();

export function useBrowserAssets() {
  return useQuery({
    queryKey: queryKeys.browser.assets(),
    queryFn: async ({ signal }) => {
      const response = await port.getBrowserAssets(signal);
      return response.data;
    },
    retry: 2,
  });
}

export function useBrowserRecords(
  engine: EngineType,
  assetId: string,
  pageIndex: number,
  pageSize: number,
  filters?: RecordFilter[],
  sort?: RecordSort,
) {
  return useQuery({
    queryKey: [
      ...queryKeys.browser.records(engine, assetId),
      { pageIndex, pageSize, filters, sort },
    ],
    queryFn: ({ signal }) =>
      port.getRecords(engine, assetId, pageIndex, pageSize, filters, sort, signal),
    enabled: engine.length > 0 && assetId.length > 0,
    retry: 2,
  });
}

export function useRecordDetail(
  engine: EngineType,
  assetId: string,
  recordId: string,
) {
  return useQuery({
    queryKey: queryKeys.browser.record(engine, assetId, recordId),
    queryFn: async ({ signal }) => {
      const response = await port.getRecord(engine, assetId, recordId, signal);
      return response.data;
    },
    enabled: engine.length > 0 && assetId.length > 0 && recordId.length > 0,
    retry: 2,
  });
}
