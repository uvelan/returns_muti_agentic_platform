import { apiClient } from "./client";

/**
 * Per-agent configuration.
 *
 * CFG-5b repointed this at the live `RETURN_PLATFORM.agents` section --
 * `AgentRegistry.build()` and every agent class read only
 * `ReturnPlatformConfiguration.agents["<id>"]`, so `manifestId` is that live
 * key (`order_discovery`, not a manifest-module id) and `document` is exactly
 * the small `AgentConfiguration` shape (`name`, `version`, `enabled`,
 * `ai_assisted`, `ai_route_ref`, plus a handful of dead knobs kept only so
 * already-published releases still parse). There is no more `moduleId`: the
 * manifest-driven module system this used to edit is retired (D-CFG-1), and
 * an agent has exactly one id now.
 *
 * `document` stays `Record<string, unknown>` rather than a typed interface:
 * the backend validates it through `AgentConfiguration`, and typing it here
 * too would be a second, weaker definition of valid that could disagree with
 * the real one. The typed table (`AgentsSection.tsx`) reads the handful of
 * fields it renders defensively for that reason.
 */

export type AgentSummary = {
  manifestId: string;
  name: string;
  version: string;
  enabled: boolean;
  aiAssisted: boolean;
  aiRouteRef: string | null;
  //: Always `"RELEASE"` now -- `agents` is a required key of the release, so
  //: there is no more a no-release state to fall back from. Kept because the
  //: screen still shows it.
  source: string;
};

export type AgentConfiguration = {
  manifestId: string;
  //: A descriptive locator (`RETURN_PLATFORM.agents.<id>`), not a filesystem
  //: path -- there is no file behind this document any more.
  path: string;
  document: Record<string, unknown>;
  source: string;
};

export type AgentConfigurationProposal = {
  proposalId: string;
  manifestId: string;
  status: string;
  risk: string;
  affectedKeys: string[];
  proposedBy: string;
  submittedAt: string;
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

  async save(
    manifestId: string,
    document: Record<string, unknown>,
  ): Promise<AgentConfigurationProposal> {
    const response = await apiClient<AgentConfigurationProposal>(
      `/api/agents/${encodeURIComponent(manifestId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document }),
      },
    );
    if (!response.data) throw new Error("The agent configuration proposal could not be submitted.");
    return response.data;
  },
};
