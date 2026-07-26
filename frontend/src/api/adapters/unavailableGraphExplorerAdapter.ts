import { type GraphExplorerPort } from "./graphExplorerPort";
import { type GraphSearchResult, type GraphNode, type GraphRelationship } from "../../contracts/graphExplorer";
import { APIError } from "../client";

export function createUnavailableGraphExplorerAdapter(): GraphExplorerPort {
  const throwUnavailable = async () => {
    await Promise.resolve();
    throw new APIError("Graph Explorer backend is not yet available.", 503, "unavailable-graph-adapter");
  };

  return {
    async searchExactId(): Promise<GraphSearchResult> {
      return throwUnavailable();
    },

    async getNode(): Promise<GraphNode> {
      return throwUnavailable();
    },

    async getRelationship(): Promise<GraphRelationship> {
      return throwUnavailable();
    },

    async expandNeighborhood(): Promise<GraphSearchResult> {
      return throwUnavailable();
    }
  };
}
