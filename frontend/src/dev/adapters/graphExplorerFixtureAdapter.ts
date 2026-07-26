import { type GraphExplorerPort } from "../../api/adapters/graphExplorerPort";
import { type GraphSearchResult, type GraphNode, type GraphRelationship } from "../../contracts/graphExplorer";
import { exactIdSearchFixture, getNodeFixture, getRelationshipFixture, expandNeighborhoodFixture } from "../../fixtures/graphExplorer";

export function createGraphExplorerFixtureAdapter(): GraphExplorerPort {
  return {
    async searchExactId(id: string): Promise<GraphSearchResult> {
      await Promise.resolve();
      return exactIdSearchFixture(id);
    },

    async getNode(nodeId: string): Promise<GraphNode> {
      await Promise.resolve();
      return getNodeFixture(nodeId);
    },

    async getRelationship(relationshipId: string): Promise<GraphRelationship> {
      await Promise.resolve();
      return getRelationshipFixture(relationshipId);
    },

    async expandNeighborhood(nodeId: string, options?: { expansionDepth?: number }): Promise<GraphSearchResult> {
      await Promise.resolve();
      return expandNeighborhoodFixture(nodeId, options?.expansionDepth);
    }
  };
}
