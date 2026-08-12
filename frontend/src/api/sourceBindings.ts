import { apiClient } from "./client";

/**
 * Where each dataset the schema names is actually read from.
 *
 * The other half of the graph shape / source binding split. A schema's shape is
 * designed once and approved by a human; a binding is an infrastructure fact
 * that changes when infrastructure changes, and until this surface existed
 * pointing an entity at a restored collection meant editing the schema file and
 * redeploying.
 *
 * Reads return the *resolved* catalogue -- configuration plus whatever has been
 * deliberately changed -- because "nobody has overridden anything" is not an
 * answer to "where does this come from".
 */

export type SourceBinding = {
  dataset: string;
  sourceAssetId: string;
  connectorType: string;
  connectionRef: string;
  objectRef: Record<string, string>;
  incrementalCursorField: string | null;
  /** Deliberately changed, rather than what the configured schema says. */
  overridden: boolean;
};

export type RebindInput = {
  sourceAssetId: string;
  connectorType: string;
  connectionRef: string;
  objectRef: Record<string, string>;
  incrementalCursorField?: string | null;
};

export const sourceBindingsApi = {
  async list(): Promise<SourceBinding[]> {
    const response = await apiClient<SourceBinding[]>("/api/source-bindings");
    return response.data ?? [];
  },

  async rebind(dataset: string, input: RebindInput): Promise<void> {
    await apiClient(`/api/source-bindings/${encodeURIComponent(dataset)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },

  /** Return a dataset to whatever the configured schema says. */
  async clear(dataset: string): Promise<void> {
    await apiClient(`/api/source-bindings/${encodeURIComponent(dataset)}`, { method: "DELETE" });
  },
};
