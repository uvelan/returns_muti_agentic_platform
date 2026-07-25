import { type GraphSearchResult, type GraphNode, type GraphRelationship } from "../contracts/graphExplorer";

// Generic helper to create metadata
function createMeta() {
  return {
    schema_version: "1.0",
    request_id: `req-${Math.random().toString(36).substring(2, 9)}`,
    generated_at: new Date().toISOString(),
    freshness: "realtime",
    partial: false,
    warnings: [],
  };
}

export function exactIdSearchFixture(id: string): GraphSearchResult {
  if (id === "error") {
    throw new Error("Simulated hard error");
  }

  if (id === "not-found") {
    return {
      data: { nodes: [], relationships: [] },
      meta: createMeta()
    };
  }

  const node: GraphNode = {
    id,
    labels: ["Customer", "Person"],
    properties: {
      name: "Alice Smith",
      email: "alice@example.com",
      status: "ACTIVE"
    },
    provenance: {
      source_id: "src-123",
      document_id: "doc-456"
    },
    ownership: {
      owner: "sales"
    }
  };

  return {
    data: {
      nodes: [node],
      relationships: []
    },
    meta: {
      ...createMeta(),
      limits: {
        maxNodes: 100,
        maxRelationships: 500,
        maxDepth: 3,
        expansionLimit: 50
      },
      isPartial: false,
      isTruncated: false
    }
  };
}

export function getNodeFixture(nodeId: string): GraphNode {
  return {
    id: nodeId,
    labels: ["Order", "Transaction"],
    properties: {
      amount: 150.00,
      currency: "USD",
      date: "2026-07-20T10:00:00Z"
    },
    provenance: {
      source_id: "src-order",
      document_id: "doc-order-1"
    }
  };
}

export function getRelationshipFixture(relationshipId: string): GraphRelationship {
  return {
    id: relationshipId,
    type: "PURCHASED",
    startNodeId: "node-customer-1",
    endNodeId: "node-order-1",
    properties: {
      channel: "online",
      timestamp: "2026-07-20T10:00:00Z"
    }
  };
}

export function expandNeighborhoodFixture(nodeId: string, depth = 1): GraphSearchResult {
  // Use depth to simulate different limits
  const isTruncated = depth > 1;

  const rootNode = getNodeFixture(nodeId);
  const relatedNode: GraphNode = {
    id: "related-1",
    labels: ["Product"],
    properties: { name: "Widget" }
  };
  const rel: GraphRelationship = {
    id: "rel-1",
    type: "CONTAINS",
    startNodeId: nodeId,
    endNodeId: "related-1",
    properties: { quantity: 2 }
  };

  return {
    data: {
      nodes: [rootNode, relatedNode],
      relationships: [rel]
    },
    meta: {
      ...createMeta(),
      limits: {
        maxNodes: 100,
        maxRelationships: 500,
        maxDepth: 3,
        expansionLimit: 50
      },
      isPartial: false,
      isTruncated
    }
  };
}
