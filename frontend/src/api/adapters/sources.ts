import { type DataSourcesPort, type SourceDetailResponse, type SourceListResponse } from "../../contracts/sources";
import { apiClient } from "../client";

export class HttpDataSourcesAdapter implements DataSourcesPort {
  async getSources(signal?: AbortSignal): Promise<SourceListResponse> {
    const response = await apiClient<SourceListResponse["data"]>("/data-console/v1/sources", {
      signal,
    });
    return { data: response.data, meta: response.meta } as SourceListResponse;
  }

  async getSource(sourceId: string, signal?: AbortSignal): Promise<SourceDetailResponse> {
    const response = await apiClient<SourceDetailResponse["data"]>(`/data-console/v1/sources/${sourceId}`, {
      signal,
    });
    return { data: response.data, meta: response.meta } as SourceDetailResponse;
  }
}

export function createDataSourcesPort(): DataSourcesPort {
  return new HttpDataSourcesAdapter();
}

