import { useQuery } from "@tanstack/react-query";

import type { APIResponse } from "../contracts/api";
import type { UnifiedInventory } from "../contracts/inventory";
import { APIError, apiClient } from "./client";


export const inventoryKeys = {
  all: ["inventory"] as const,
  unified: () => [...inventoryKeys.all, "unified"] as const,
};


async function fetchInventory(
  signal: AbortSignal,
): Promise<APIResponse<UnifiedInventory>> {
  const response = await apiClient<UnifiedInventory>(
    "/data-console/v1/inventory",
    { method: "GET", signal },
  );

  if (response.data === null) {
    throw new APIError(
      "The inventory endpoint returned no data.",
      502,
      response.meta.request_id,
    );
  }

  return response;
}


export function useUnifiedInventory() {
  return useQuery({
    queryKey: inventoryKeys.unified(),
    queryFn: ({ signal }) => fetchInventory(signal),
    staleTime: 30_000,
  });
}

export function useInventoryAsset(engine: string, assetId: string) {
  return useQuery({
    queryKey: [...inventoryKeys.all, "asset", engine, assetId] as const,
    queryFn: ({ signal }) => apiClient<import("../contracts/inventory").InventoryDetail>(
      `/data-console/v1/inventory/${encodeURIComponent(engine)}/${encodeURIComponent(assetId)}`,
      { signal },
    ),
    select: (response) => response.data,
    enabled: engine.length > 0 && assetId.length > 0,
    staleTime: 30_000,
  });
}
