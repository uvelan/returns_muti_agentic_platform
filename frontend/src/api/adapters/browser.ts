/* eslint-disable */
import { type EngineType } from "../../contracts/sources";
import { type BrowserAssetListResponse, type BrowserRecordDetailResponse, type BrowserRecordsResponse, type DataBrowserPort, type RecordFilter, type RecordSort } from "../../contracts/browser";
import { apiClient } from "../client";

export class HttpDataBrowserAdapter implements DataBrowserPort {
  async getBrowserAssets(signal?: AbortSignal): Promise<BrowserAssetListResponse> {
    const response = await apiClient<BrowserAssetListResponse["data"]>("/data-console/v1/browser", {
      signal,
    });
    return { data: response.data, meta: response.meta } as BrowserAssetListResponse;
  }

  async getRecords(engine: EngineType, assetId: string, pageCursor: string | null, pageSize: number, filters?: RecordFilter[], sort?: RecordSort, signal?: AbortSignal): Promise<BrowserRecordsResponse> {
    const queryParams = new URLSearchParams();
    if (pageCursor) queryParams.set("page_cursor", pageCursor);
    queryParams.set("page_size", String(pageSize));
    if (filters) queryParams.set("filters", JSON.stringify(filters));
    if (sort) queryParams.set("sort", `${sort.field}:${sort.direction}`);

    const url = `/data-console/v1/browser/${engine}/${assetId}/records?${queryParams.toString()}`;
    const response = await apiClient<BrowserRecordsResponse["data"]>(url, {
      signal,
    });
    return { data: response.data, meta: response.meta, page: response.page } as BrowserRecordsResponse;
  }

  async getRecord(engine: EngineType, assetId: string, recordId: string, signal?: AbortSignal): Promise<BrowserRecordDetailResponse> {
    const response = await apiClient<BrowserRecordDetailResponse["data"]>(`/data-console/v1/browser/${engine}/${assetId}/records/${recordId}`, {
      signal,
    });
    return { data: response.data, meta: response.meta } as BrowserRecordDetailResponse;
  }
}

export function createDataBrowserPort(): DataBrowserPort {
  return new HttpDataBrowserAdapter();
}

