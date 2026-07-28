import { apiClient } from "./client";

export type RuntimeConfig = {
  releaseId: string;
  environment: string;
  apiBasePath: string;
  features: {
    orderDiscoveryCopilot: boolean;
    aiStudioOperationalGeneration: boolean;
  };
  capabilities: {
    availableSourceTypes: string[];
    availableModelProviders: string[];
  };
};

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await apiClient<RuntimeConfig>("/api/v1/runtime-config");
  if (!response.data) {
    throw new Error("No runtime configuration returned from the server.");
  }
  return response.data;
}
