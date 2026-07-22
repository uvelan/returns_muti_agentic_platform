import { useQuery } from "@tanstack/react-query";

import type {
  APIResponse,
  InfrastructureOverview,
} from "../contracts/api";
import { APIError, apiClient } from "./client";


const OVERVIEW_REFETCH_INTERVAL_MS = 10_000;


export const infrastructureKeys = {
  all: ["infrastructure"] as const,

  overview: () => [
    ...infrastructureKeys.all,
    "overview",
  ] as const,
};


async function fetchInfrastructureOverview(
  signal: AbortSignal,
): Promise<APIResponse<InfrastructureOverview>> {
  const response = await apiClient<InfrastructureOverview>(
    "/data-console/v1/overview",
    {
      method: "GET",
      signal,
    },
  );

  if (response.data === null) {
    throw new APIError(
      "The infrastructure overview returned no data.",
      502,
      response.meta.request_id,
    );
  }

  return response;
}


/**
 * Fetch the infrastructure overview and refresh it every ten seconds.
 *
 * The complete API envelope is retained so the page can display partial
 * results, warnings, freshness, generation time, and correlation details.
 */
export function useInfrastructureOverview() {
  return useQuery({
    queryKey: infrastructureKeys.overview(),

    queryFn: ({ signal }) =>
      fetchInfrastructureOverview(signal),

    refetchInterval: OVERVIEW_REFETCH_INTERVAL_MS,

    // Do not continue operational polling while the browser tab is hidden.
    refetchIntervalInBackground: false,
  });
}