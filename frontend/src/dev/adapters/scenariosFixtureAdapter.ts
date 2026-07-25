import type { ScenarioPort } from "../../api/ports/scenariosPort";
import type { Scenario, ScenarioDiff, ScenarioPreviewRecord } from "../../contracts/scenarios";
import type { APIResponse } from "../../contracts/api";

const MOCK_SCENARIOS: Scenario[] = [
  {
    id: "scen-1",
    name: "Q4 +10% Growth",
    description: "What-if scenario projecting 10% volume growth over Q3 actuals.",
    baseWorkspaceId: "ws-sandbox-1",
    status: "READY",
    parameters: { growthRate: 0.1 },
    createdAt: "2026-07-22T14:00:00Z",
    owner: "alice@example.com",
    version: 1,
    validationIssues: []
  }
];

const MOCK_DIFFS: ScenarioDiff[] = [
  {
    recordId: "rec-1",
    status: "MODIFIED",
    baseData: { projectedRevenue: 150000 },
    scenarioData: { projectedRevenue: 165000 },
    issues: ["Revenue growth exceeds standard variance"]
  }
];

function makeMeta() {
  return {
    schema_version: "1.0",
    request_id: `req-scen-${String(Date.now())}`,
    generated_at: new Date().toISOString(),
    freshness: "LIVE" as const,
    partial: false,
    warnings: []
  };
}

export function createFixtureScenarioAdapter(): ScenarioPort {
  return {
    async listScenarios(_options?: { signal?: AbortSignal }): Promise<APIResponse<Scenario[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: [...MOCK_SCENARIOS], meta: makeMeta(), page: null };
    },

    async getScenario(scenarioId: string, _options?: { signal?: AbortSignal }): Promise<Scenario> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const s = MOCK_SCENARIOS.find(sc => sc.id === scenarioId);
      if (!s) throw new Error("Scenario not found");
      return s;
    },

    async createScenario(payload: { name: string; description: string; baseWorkspaceId: string; parameters: Record<string, unknown> }, _options?: { signal?: AbortSignal }): Promise<Scenario> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const newScenario: Scenario = {
        id: `scen-mock-${String(Date.now())}`,
        name: payload.name,
        description: payload.description,
        baseWorkspaceId: payload.baseWorkspaceId,
        status: "GENERATING",
        parameters: payload.parameters,
        createdAt: new Date().toISOString(),
        owner: "currentUser@example.com",
        version: 1,
        validationIssues: []
      };
      MOCK_SCENARIOS.push(newScenario);
      return newScenario;
    },

    async deleteScenario(scenarioId: string, _options?: { signal?: AbortSignal }): Promise<void> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const idx = MOCK_SCENARIOS.findIndex(s => s.id === scenarioId);
      if (idx !== -1) MOCK_SCENARIOS.splice(idx, 1);
    },

    async getScenarioDiffs(_scenarioId: string, _options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioDiff[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: [...MOCK_DIFFS], meta: makeMeta(), page: null };
    },

    async generateScenario(scenarioId: string, _options?: { signal?: AbortSignal }): Promise<Scenario> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const s = MOCK_SCENARIOS.find(sc => sc.id === scenarioId);
      if (!s) throw new Error("Scenario not found");
      return { ...s, status: "READY" };
    },

    async validateScenario(scenarioId: string, _options?: { signal?: AbortSignal }): Promise<Scenario> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const s = MOCK_SCENARIOS.find(sc => sc.id === scenarioId);
      if (!s) throw new Error("Scenario not found");
      return { ...s, status: "READY" };
    },

    async approveScenario(scenarioId: string, _options?: { signal?: AbortSignal }): Promise<Scenario> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const s = MOCK_SCENARIOS.find(sc => sc.id === scenarioId);
      if (!s) throw new Error("Scenario not found");
      return { ...s, status: "APPROVED" };
    },

    async previewScenario(_scenarioId: string, _options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioPreviewRecord[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: [], meta: makeMeta(), page: null };
    }
  };
}
