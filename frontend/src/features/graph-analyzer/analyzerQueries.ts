import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getAnalysis, getAnalyzerBootstrap, getSchemas, getSyncRun } from "../../api/graphAnalyzer";

export const analyzerKeys = {
  all: ["graph-analyzer"] as const,
  bootstrap: () => [...analyzerKeys.all, "bootstrap"] as const,
  schemas: () => [...analyzerKeys.all, "schemas"] as const,
  analysis: (id: string) => [...analyzerKeys.all, "analysis", id] as const,
  sync: (id: string) => [...analyzerKeys.all, "sync", id] as const,
};

export function useAnalyzerBootstrap() {
  return useQuery({ queryKey: analyzerKeys.bootstrap(), queryFn: ({ signal }) => getAnalyzerBootstrap(signal) });
}

export function useSchemas() {
  return useQuery({ queryKey: analyzerKeys.schemas(), queryFn: ({ signal }) => getSchemas(signal) });
}

export function useAnalysis(runId: string | null) {
  return useQuery({
    queryKey: analyzerKeys.analysis(runId ?? "none"),
    queryFn: ({ signal }) => getAnalysis(runId ?? "", signal),
    enabled: runId !== null,
    refetchInterval: (query) => query.state.data?.status === "RUNNING" ? 1_500 : false,
  });
}

export function useSyncRun(runId: string | null) {
  return useQuery({
    queryKey: analyzerKeys.sync(runId ?? "none"),
    queryFn: ({ signal }) => getSyncRun(runId ?? "", signal),
    enabled: runId !== null,
    refetchInterval: (query) => ["PREPARING", "RUNNING"].includes(query.state.data?.status ?? "") ? 1_500 : false,
  });
}

export function useAnalyzerMutation<TInput, TResult>(mutationFn: (input: TInput) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: analyzerKeys.all });
    },
  });
}
