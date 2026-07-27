import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "./client";

export type ValidationReceipt = {
  receipt_id: string;
  target_uri: string;
  subject_type: string;
  subject_key: string;
  verified_at: string;
  valid_until: string;
  verified_by: string;
  status: "PASSED";
  checksum_sha256: string;
  configuration_checksum?: string | null;
  secret_fingerprint?: string | null;
  secret_version?: number | null;
  tests: string[];
}

export type AIValidationPayload = {
  provider: "GOOGLE" | "NVIDIA" | "OPENAI" | "ANTHROPIC";
  modelId: string;
  modelClass: "LIGHTWEIGHT" | "STANDARD";
  taskKey: string;
  apiKey: string;
  vaultReference: string;
}

export type DataSourceValidationPayload = {
  sourceKey: string;
  sourceType: "MONGODB" | "NEO4J" | "SQLSERVER";
  accessMode: "READ_ONLY" | "READ_WRITE";
  host?: string;
  port?: number;
  uri?: string;
  username?: string;
  database: string;
  requiredDatasets: string[];
  credential: string;
  credentialKind: "DSN" | "PASSWORD";
  vaultReference: string;
}

const receiptKey = ["console", "runtime-validation", "receipts"] as const;

export function useValidationReceipts() {
  return useQuery({
    queryKey: receiptKey,
    queryFn: ({ signal }) =>
      apiClient<ValidationReceipt[]>("/data-console/v1/runtime-validation/receipts", { signal }),
    select: (response) => response.data ?? [],
    staleTime: 5_000,
  });
}

export function useValidateAIConfiguration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AIValidationPayload) => {
      const response = await apiClient<ValidationReceipt>(
        "/data-console/v1/runtime-validation/ai/validate-and-stage",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      return response.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: receiptKey }),
  });
}

export function useValidateDataSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DataSourceValidationPayload) => {
      const response = await apiClient<ValidationReceipt>(
        "/data-console/v1/runtime-validation/data-sources/validate-and-stage",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      return response.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: receiptKey }),
  });
}
