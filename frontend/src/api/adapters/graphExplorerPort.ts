import type { GraphSearchResult, GraphNode, GraphRelationship } from "../../contracts/graphExplorer";

export type GraphExplorerPort = {
  searchExactId(id: string, options?: { expansionDepth?: number; signal?: AbortSignal }): Promise<GraphSearchResult>;
  getNode(nodeId: string, options?: { signal?: AbortSignal }): Promise<GraphNode>;
  getRelationship(relationshipId: string, options?: { signal?: AbortSignal }): Promise<GraphRelationship>;
  expandNeighborhood(nodeId: string, options?: { expansionDepth?: number; signal?: AbortSignal }): Promise<GraphSearchResult>;
}
