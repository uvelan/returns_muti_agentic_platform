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
