import { describe, expect, it } from "vitest";

import type { DraftShapeView } from "../../api/graphSchema";
import { buildFlowElements } from "./schemaFlowModel";

const SHAPE = {
  entities: {
    Zebra: {
      label: "Zebra",
      source_dataset: "zebras",
      properties: {
        zebra_id: {
          type: "STRING",
          source_field: "zebra_id",
          transformation: "NONE",
        },
      },
      identifier_properties: ["zebra_id"],
      ownership: "SOURCE",
      sync_mode: "INCREMENTAL",
    },
    Alpha: {
      label: "Alpha",
      source_dataset: "alphas",
      properties: {},
      identifier_properties: [],
      ownership: "SOURCE",
      sync_mode: "FULL",
    },
  },
  relationships: [
    {
      relationship_type: "LINKS_TO",
      from_label: "Alpha",
      to_label: "Missing",
      cardinality: "ONE_TO_MANY",
    },
    {
      relationship_type: "LINKS_TO",
      from_label: "Alpha",
      to_label: "Missing",
      cardinality: "ONE_TO_MANY",
    },
  ],
  graph_indexes: [],
  graph_constraints: [],
} satisfies DraftShapeView;

describe("buildFlowElements", () => {
  it("uses stable sorted node ids and deterministic client-only positions", () => {
    const first = buildFlowElements(SHAPE);
    const second = buildFlowElements(SHAPE);

    expect(first.nodes.map((node) => node.id)).toEqual(["Alpha", "Zebra", "Missing"]);
    expect(first.nodes.map((node) => node.position)).toEqual([
      { x: 0, y: 0 },
      { x: 330, y: 0 },
      { x: 660, y: 0 },
    ]);
    expect(second).toEqual(first);
  });

  it("keeps duplicate parallel relationships distinct", () => {
    const { edges } = buildFlowElements(SHAPE);

    expect(edges).toHaveLength(2);
    expect(edges[0].id).toBe("Alpha::LINKS_TO::Missing::0");
    expect(edges[1].id).toBe("Alpha::LINKS_TO::Missing::1");
  });

  it("represents a missing endpoint without fabricating entity details", () => {
    const missing = buildFlowElements(SHAPE).nodes.find((node) => node.id === "Missing");

    expect(missing?.data.referenced).toBe(true);
    expect(missing?.data.entity).toBeNull();
  });
});
