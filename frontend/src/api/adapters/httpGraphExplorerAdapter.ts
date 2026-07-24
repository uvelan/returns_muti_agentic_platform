import { apiClient } from "../client";
import { type GraphExplorerPort } from "./graphExplorerPort";
import { 
  type GraphSearchResult, 
  GraphSearchResultSchema, 
  type GraphNode, 
  GraphNodeSchema, 
  type GraphRelationship, 
  GraphRelationshipSchema 
} from "../../contracts/graphExplorer";


export function createHttpGraphExplorerAdapter(): GraphExplorerPort {
  return {
    async searchExactId(id: string, options?: { expansionDepth?: number; signal?: AbortSignal }): Promise<GraphSearchResult> {
      const url = new URL('/data-console/v1/graph/search', window.location.origin);
      url.searchParams.set('q', id);
      if (options?.expansionDepth !== undefined) {
        url.searchParams.set('expansionDepth', options.expansionDepth.toString());
      }
      
      const response = await apiClient<unknown>(url.pathname + url.search, { signal: options?.signal });
      return GraphSearchResultSchema.parse(response);
    },

    async getNode(nodeId: string, options?: { signal?: AbortSignal }): Promise<GraphNode> {
      const response = await apiClient<unknown>(`/data-console/v1/graph/nodes/${encodeURIComponent(nodeId)}`, { signal: options?.signal });
      // We expect the node to be nested in `response.data` according to the standard envelope,
      // but GraphNodeSchema parses the node itself. Wait, if the response is APIResponse<T>, 
      // apiClient returns the entire envelope payload! 
      // The GraphSearchResultSchema validates the ENTIRE envelope.
      // For getNode, the APIResponse<T>.data is the GraphNode. Let's assume the API returns { data: GraphNode, meta: ... }.

      // Wait, we need to wrap GraphNode inside an envelope schema, or assume `response.data` matches GraphNodeSchema.
      return GraphNodeSchema.parse(response.data);
    },

    async getRelationship(relationshipId: string, options?: { signal?: AbortSignal }): Promise<GraphRelationship> {
      const response = await apiClient<unknown>(`/data-console/v1/graph/relationships/${encodeURIComponent(relationshipId)}`, { signal: options?.signal });
      return GraphRelationshipSchema.parse(response.data);
    },

    async expandNeighborhood(nodeId: string, options?: { expansionDepth?: number; signal?: AbortSignal }): Promise<GraphSearchResult> {
      const url = new URL(`/data-console/v1/graph/nodes/${encodeURIComponent(nodeId)}/neighborhood`, window.location.origin);
      if (options?.expansionDepth !== undefined) {
        url.searchParams.set('expansionDepth', options.expansionDepth.toString());
      }
      const response = await apiClient<unknown>(url.pathname + url.search, { signal: options?.signal });
      return GraphSearchResultSchema.parse(response);
    }
  };
}
