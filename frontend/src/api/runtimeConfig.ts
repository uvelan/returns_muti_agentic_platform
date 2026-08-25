import { apiClient } from "./client";

export type RuntimeConfig = {
  releaseId: string;
  environment: string;
  apiBasePath: string;
  features: {
    orderDiscoveryCopilot: boolean;
  };
  capabilities: {
    availableSourceTypes: string[];
    availableModelProviders: string[];
  };
  /**
   * Which agent the shell addresses. Served, never compiled in: the Copilot
   * used to send the literal `"order_discovery"` while the active schema keys
   * the policy `order-discovery-agent`, so every turn 422'd.
   *
   * `null` means the deployment has not stated the mapping; the whole block is
   * optional because a backend older than this field answers without it. Both
   * are the same instruction to a caller -- fail closed. There is no other
   * literal to fall back to, which is the point.
   */
  agents?: {
    orderDiscovery: string | null;
  };
  /**
   * The reason and condition catalogues an associate may pick a line from.
   *
   * Served, never compiled in. `POST /api/cases/{id}/selected-items` refuses a
   * term the active release does not publish with
   * `422 SELECTION_TERM_NOT_PUBLISHED`, so a picker built from a list in this
   * repository would offer terms the writer rejects the moment an operator
   * edits the catalogue -- which is the hardcoded catalogue that was removed
   * from the item-selection pane.
   *
   * Optional because a backend older than this field answers without it, and
   * **empty is meaningful**: it says this deployment has published no
   * catalogue, which the writer reads as "refuse nothing". A client seeing
   * empty must offer no choices rather than invent some.
   */
  selectionVocabulary?: {
    reasons: string[];
    conditions: string[];
  };
  /**
   * The order the operator ranked the conversation's facts in.
   *
   * `clarification_policy.fields[].priority`, descending, ties broken by field
   * name -- the ranking of the same list that decides which fact names a turn
   * may capture at all, so every name in `captured_facts` is a name this list
   * can place. Served, never compiled in: the facts panel used to list its rows
   * in an order written into a TypeScript array, so re-ranking the policy moved
   * what the agent asks for next and moved nothing on the screen reporting it.
   *
   * Optional because a backend older than this field answers without it, and
   * **empty is meaningful**: it says this deployment stated no ranking, and a
   * client seeing empty must fall back to an order it can defend rather than
   * substitute one of its own.
   */
  factCatalogue?: {
    orderedFields: string[];
  };
  /**
   * Which candidate fields the Copilot's match table leads with; everything
   * else a row carries waits behind its Details control.
   *
   * Served, never compiled in: which fields identify an order to an associate
   * is an operator decision that changes in a release, not in a frontend
   * deploy. Each column's `fields` is an alias chain -- the first name the row
   * carries supplies the value -- because order, line and customer searches
   * return differently shaped rows one column must read across.
   *
   * Optional because a backend older than this field answers without it, and
   * **empty is meaningful**: the deployment has not said, and the client falls
   * back to the identity columns it can defend rather than to rendering every
   * field the query selected.
   */
  candidateColumns?: { label: string; fields: string[] }[];
};

/**
 * The configured fact ranking, or empty when the deployment published none.
 *
 * Empty is a real answer and not an error; `extractedReturnFields` documents
 * what it does with it.
 */
export function capturedFactOrder(runtimeConfig: RuntimeConfig | null): readonly string[] {
  return runtimeConfig?.factCatalogue?.orderedFields ?? [];
}

/** The published catalogues, or empty when the deployment has published none. */
export function selectionVocabulary(
  runtimeConfig: RuntimeConfig | null,
): { readonly reasons: readonly string[]; readonly conditions: readonly string[] } {
  return {
    reasons: runtimeConfig?.selectionVocabulary?.reasons ?? [],
    conditions: runtimeConfig?.selectionVocabulary?.conditions ?? [],
  };
}

/**
 * The operator's candidate-table columns, or empty when the deployment
 * published none. Empty is a real answer: `CandidateOrderMode` documents the
 * fallback it defends.
 */
export function candidateColumnCatalogue(
  runtimeConfig: RuntimeConfig | null,
): readonly { readonly label: string; readonly fields: readonly string[] }[] {
  return runtimeConfig?.candidateColumns ?? [];
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await apiClient<RuntimeConfig>("/api/runtime-config");
  if (!response.data) {
    throw new Error("No runtime configuration returned from the server.");
  }
  return response.data;
}
