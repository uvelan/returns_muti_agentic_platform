import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { graphExplorerPort } from "./adapters/graphExplorer";
import { queryKeys } from "./queryKeyFactory";
import { type GraphSearchResult, type GraphNode, type GraphRelationship } from "../contracts/graphExplorer";

export function useGraphSearch(id: string, expansionDepth?: number): UseQueryResult<GraphSearchResult> {
  return useQuery({
    queryKey: queryKeys.graphExplorer.search(id, expansionDepth),
    queryFn: ({ signal }) => graphExplorerPort.searchExactId(id, { signal, expansionDepth }),
    enabled: Boolean(id)
  });
}

export function useGraphNode(nodeId: string): UseQueryResult<GraphNode> {
  return useQuery({
    queryKey: queryKeys.graphExplorer.node(nodeId),
    queryFn: ({ signal }) => graphExplorerPort.getNode(nodeId, { signal }),
    enabled: Boolean(nodeId)
  });
}

export function useGraphRelationship(relationshipId: string): UseQueryResult<GraphRelationship> {
  return useQuery({
    queryKey: queryKeys.graphExplorer.relationship(relationshipId),
    queryFn: ({ signal }) => graphExplorerPort.getRelationship(relationshipId, { signal }),
    enabled: Boolean(relationshipId)
  });
}

export function useGraphNeighborhood(nodeId: string, expansionDepth?: number): UseQueryResult<GraphSearchResult> {
  return useQuery({
    queryKey: queryKeys.graphExplorer.neighborhood(nodeId, expansionDepth),
    queryFn: ({ signal }) => graphExplorerPort.expandNeighborhood(nodeId, { signal, expansionDepth }),
    enabled: Boolean(nodeId)
  });
}
