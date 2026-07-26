import { type ScenarioPort } from "../ports/scenariosPort";
import { type Scenario, type ScenarioDiff, type ScenarioPreviewRecord } from "../../contracts/scenarios";
import { type APIResponse } from "../../contracts/api";
import { apiClient } from "../client";

export class HttpScenarioAdapter implements ScenarioPort {
  async listScenarios(options?: { signal?: AbortSignal }): Promise<APIResponse<Scenario[]>> {
    const response = await apiClient<Scenario[]>(`/data-console/v1/scenarios`, { signal: options?.signal });
    return response;
  }

  async getScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario> {
    const response = await apiClient<Scenario>(`/data-console/v1/scenarios/${scenarioId}`, { signal: options?.signal });
    if (response.data === null) throw new Error('Unexpected null response');
    return response.data;

  }

  async createScenario(payload: { name: string; description: string; baseWorkspaceId: string; parameters: Record<string, unknown> }, options?: { signal?: AbortSignal }): Promise<Scenario> {
    const response = await apiClient<Scenario>(`/data-console/v1/scenarios`, {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    if (response.data === null) throw new Error('Unexpected null response');
    return response.data;

  }

  async deleteScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<void> {
    await apiClient(`/data-console/v1/scenarios/${scenarioId}`, {
      method: "DELETE",
      signal: options?.signal,
    });
  }

  async getScenarioDiffs(scenarioId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioDiff[]>> {
    const response = await apiClient<ScenarioDiff[]>(`/data-console/v1/scenarios/${scenarioId}/diffs`, { signal: options?.signal });
    return response;
  }

  async generateScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario> {
    const response = await apiClient<Scenario>(`/data-console/v1/scenarios/${scenarioId}/generate`, {
      method: "POST",
      signal: options?.signal,
    });
    if (response.data === null) throw new Error('Unexpected null response');
    return response.data;

  }

  async validateScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario> {
    const response = await apiClient<Scenario>(`/data-console/v1/scenarios/${scenarioId}/validate`, {
      method: "POST",
      signal: options?.signal,
    });
    if (response.data === null) throw new Error('Unexpected null response');
    return response.data;

  }

  async approveScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<Scenario> {
    const response = await apiClient<Scenario>(`/data-console/v1/scenarios/${scenarioId}/approve`, {
      method: "POST",
      signal: options?.signal,
    });
    if (response.data === null) throw new Error('Unexpected null response');
    return response.data;

  }

  async previewScenario(scenarioId: string, options?: { signal?: AbortSignal }): Promise<APIResponse<ScenarioPreviewRecord[]>> {
    const response = await apiClient<ScenarioPreviewRecord[]>(`/data-console/v1/scenarios/${scenarioId}/preview`, { signal: options?.signal });
    return response;
  }
}
