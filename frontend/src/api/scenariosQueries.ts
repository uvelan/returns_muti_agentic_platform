/* eslint-disable @typescript-eslint/no-invalid-void-type, react-hooks/rules-of-hooks */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createScenarioAdapters } from "./adapters/scenarios";
import { queryKeys } from "./queryKeyFactory";
import type { Scenario } from "../contracts/scenarios";

const adapter = createScenarioAdapters();

export function useScenariosList() {
  return useQuery({ queryKey: queryKeys.scenarios.list(), queryFn: ({ signal }) => adapter.listScenarios({ signal }), select: (response) => response.data ?? [] });
}
export function useScenarioDetail(scenarioId: string) {
  return useQuery({ queryKey: queryKeys.scenarios.detail(scenarioId), queryFn: ({ signal }) => adapter.getScenario(scenarioId, { signal }), enabled: scenarioId.length > 0 });
}
export function useScenarioDiffs(scenarioId: string) {
  return useQuery({ queryKey: queryKeys.scenarios.diffs(scenarioId), queryFn: ({ signal }) => adapter.getScenarioDiffs(scenarioId, { signal }), select: (response) => response.data ?? [], enabled: scenarioId.length > 0 });
}
export function useScenarioPreview(scenarioId: string) {
  return useQuery({ queryKey: [...queryKeys.scenarios.detail(scenarioId), "preview"], queryFn: ({ signal }) => adapter.previewScenario(scenarioId, { signal }), select: (response) => response.data ?? [], enabled: scenarioId.length > 0 });
}
export function useCreateScenario() {
  const queryClient = useQueryClient();
  return useMutation<Scenario, Error, { name: string; description: string; baseWorkspaceId: string; parameters: Record<string, unknown> }>({ mutationFn: (payload) => adapter.createScenario(payload), onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.list() }) });
}
export function useDeleteScenario() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({ mutationFn: (scenarioId) => adapter.deleteScenario(scenarioId), onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.list() }) });
}
function lifecycleMutation(action: "generate" | "validate" | "approve") {
  const queryClient = useQueryClient();
  return useMutation<Scenario, Error, string>({
    mutationFn: (scenarioId) => action === "generate" ? adapter.generateScenario(scenarioId) : action === "validate" ? adapter.validateScenario(scenarioId) : adapter.approveScenario(scenarioId),
    onSuccess: (scenario) => {
      queryClient.setQueryData(queryKeys.scenarios.detail(scenario.id), scenario);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.list() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.diffs(scenario.id) }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.scenarios.detail(scenario.id), "preview"] }),
      ]);
    },
  });
}
export function useGenerateScenario() { return lifecycleMutation("generate"); }
export function useValidateScenario() { return lifecycleMutation("validate"); }
export function useApproveScenario() { return lifecycleMutation("approve"); }
