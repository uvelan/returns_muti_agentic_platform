import { apiClient } from "./client";

/**
 * The calling principal, as reported by `GET /api/principal` (Phase 17).
 *
 * `roles` is here for display -- "signed in as X with role Y" -- and must not
 * be branched on. Every visibility and enablement decision goes through
 * `capabilities`, so adding a role never means editing a screen.
 */
export type PrincipalView = {
  readonly subject: string;
  readonly roles: readonly string[];
  readonly capabilities: readonly string[];
};

/**
 * Capability names, mirroring `security/capabilities.py`.
 *
 * Typed as a union rather than `string` so a typo in a screen is a compile
 * error instead of a silently-never-granted permission that hides a button in
 * production. The backend remains authoritative: this list existing does not
 * grant anything, and every capability is re-checked server-side.
 */
export type Capability =
  | "returns.session.read"
  | "returns.session.write"
  | "returns.support.act"
  | "returns.logistics.act"
  | "returns.warehouse.act"
  | "returns.audit.read"
  | "config.runtime.read"
  | "config.release.read"
  | "config.release.promote"
  | "config.source.read"
  | "config.source.write"
  | "graph_schema.draft.read"
  | "graph_schema.draft.write"
  | "graph_schema.generation.activate"
  | "governance.proposal.read"
  | "governance.proposal.write"
  | "governance.proposal.approve"
  | "governance.proposal.activate"
  | "ai.request.read"
  | "ai.metrics.read"
  | "ai.interception.read"
  | "ai.interception.act"
  | "ai.replay.read"
  | "ai.route.write";

export async function fetchPrincipal(): Promise<PrincipalView> {
  const response = await apiClient<PrincipalView>("/api/principal");
  if (!response.data) {
    throw new Error("No principal returned from the server.");
  }
  return response.data;
}
