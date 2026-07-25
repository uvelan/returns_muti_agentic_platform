import type { Scenario, ScenarioDiff, ScenarioPreviewRecord } from "../../contracts/scenarios";
import type { APIResponse } from "../../contracts/api";

export type ScenarioPort = {
  listScenarios(options?: { signal?: AbortSignal }): Promise<APIResponse<Scenario[]>>;
  getScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario>;
  createScenario(payload: { name: string; description: string; baseWorkspaceId: string; parameters: Record<string, unknown> }, options?: { signal?: AbortSignal }): Promise<Scenario>;
  deleteScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<void>;
  getScenarioDiffs(scenarioId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioDiff[]>>;

  generateScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario>;
  validateScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario>;
  approveScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario>;
  previewScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioPreviewRecord[]>>;
};
