import { apiClient } from "./client";

/**
 * Per-agent configuration.
 *
 * Each agent has had its own module file under `backend/config/agents/` all
 * along, declared in the manifest -- the separation was already there and
 * nothing served it, so the console could neither show an agent's settings nor
 * change them.
 *
 * The document is `unknown`-shaped on purpose. A module's payload differs by
 * agent and the backend validates it through the loader the platform boots
 * from; typing it here would be a second, weaker definition of valid that
 * could disagree with the real one.
 */

export type AgentSummary = {
  manifestId: string;
  moduleId: string;
  name: string;
  enabled: boolean;
  status: string;
  configurationVersion: string;
};

export type AgentConfiguration = {
  manifestId: string;
  moduleId: string;
  path: string;
  document: Record<string, unknown>;
};

export const agentConfigApi = {
  async list(): Promise<AgentSummary[]> {
    const response = await apiClient<AgentSummary[]>("/api/agents");
    return response.data ?? [];
  },

  async read(manifestId: string): Promise<AgentConfiguration> {
    const response = await apiClient<AgentConfiguration>(
      `/api/agents/${encodeURIComponent(manifestId)}`,
    );
    if (!response.data) throw new Error("The agent configuration could not be read.");
    return response.data;
  },

  async save(manifestId: string, document: Record<string, unknown>): Promise<AgentConfiguration> {
    const response = await apiClient<AgentConfiguration>(
      `/api/agents/${encodeURIComponent(manifestId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document }),
      },
    );
    if (!response.data) throw new Error("The agent configuration could not be saved.");
    return response.data;
  },
};
