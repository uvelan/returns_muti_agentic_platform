import {
  type BrowserAssetListResponse,
  type BrowserRecordDetailResponse,
  type BrowserRecordsResponse,
  type DataBrowserPort,
  type RecordFilter,
  type RecordSort,
} from "../../contracts/browser";
import { type EngineType } from "../../contracts/sources";
import { apiClient } from "../client";

export class HttpDataBrowserAdapter implements DataBrowserPort {
  async getBrowserAssets(signal?: AbortSignal): Promise<BrowserAssetListResponse> {
    const response = await apiClient<BrowserAssetListResponse["data"]>(
      "/data-console/v1/browser/assets",
      { signal },
    );
    return { data: response.data, meta: response.meta, page: response.page ?? null };
  }

  async getRecords(
    engine: EngineType,
    assetId: string,
    pageIndex: number,
    pageSize: number,
    _filters?: RecordFilter[],
    _sort?: RecordSort,
    signal?: AbortSignal,
  ): Promise<BrowserRecordsResponse> {
    const query = new URLSearchParams({
      page_index: String(pageIndex),
      page_size: String(pageSize),
    });
    const url =
      `/data-console/v1/browser/${encodeURIComponent(engine)}/` +
      `${encodeURIComponent(assetId)}/records?${query.toString()}`;
    const response = await apiClient<BrowserRecordsResponse["data"]>(url, { signal });
    return { data: response.data, meta: response.meta, page: response.page };
  }

  async getRecord(
    engine: EngineType,
    assetId: string,
    recordId: string,
    signal?: AbortSignal,
  ): Promise<BrowserRecordDetailResponse> {
    const url =
      `/data-console/v1/browser/${encodeURIComponent(engine)}/` +
      `${encodeURIComponent(assetId)}/records/${encodeURIComponent(recordId)}`;
    const response = await apiClient<BrowserRecordDetailResponse["data"]>(url, {
      signal,
    });
    return { data: response.data, meta: response.meta, page: response.page ?? null };
  }
}

export function createDataBrowserPort(): DataBrowserPort {
  return new HttpDataBrowserAdapter();
}
