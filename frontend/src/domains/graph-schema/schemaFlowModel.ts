import { MarkerType, type Edge, type Node } from "@xyflow/react";

import type { DraftShapeView, EntityShapeView } from "../../api/graphSchema";

type SchemaNodeData = {
  [key: string]: unknown;
  label: string;
  entity: EntityShapeView | null;
  referenced: boolean;
};

export type SchemaFlowNode = Node<SchemaNodeData, "schemaEntity">;

export type SchemaFlowElements = {
  nodes: SchemaFlowNode[];
  edges: Edge[];
};

/**
 * Converts the published shape into deterministic, client-only positions.
 * Coordinates are never persisted and communicate no semantic ordering.
 */
export function buildFlowElements(shape: DraftShapeView): SchemaFlowElements {
  const entityLabels = Object.keys(shape.entities).sort(
    (left, right) => left.localeCompare(right),
  );
  const referencedLabels = Array.from(new Set(
    shape.relationships.flatMap((relationship) => [
      relationship.from_label,
      relationship.to_label,
    ]),
  ))
    .filter((label) => !(label in shape.entities))
    .sort((left, right) => left.localeCompare(right));
  const labels = [...entityLabels, ...referencedLabels];

  const nodes: SchemaFlowNode[] = labels.map((label, index) => ({
    id: label,
    type: "schemaEntity",
    position: {
      x: (index % 3) * 330,
      y: Math.floor(index / 3) * 260,
    },
    data: {
      label,
      entity: shape.entities[label] ?? null,
      referenced: !(label in shape.entities),
    },
  }));

  const edges: Edge[] = shape.relationships.map((relationship, index) => ({
    id: [
      relationship.from_label,
      relationship.relationship_type,
      relationship.to_label,
      String(index),
    ].join("::"),
    source: relationship.from_label,
    target: relationship.to_label,
    type: "smoothstep",
    label: `${relationship.relationship_type} | ${relationship.cardinality}`,
    markerEnd: { type: MarkerType.ArrowClosed, color: "#004e47" },
    style: { stroke: "#466460", strokeWidth: 1.5 },
    labelStyle: { fill: "#004e47", fontSize: 10, fontWeight: 600 },
    labelBgStyle: { fill: "#ffffff", fillOpacity: 0.94 },
    labelBgPadding: [8, 5],
    labelBgBorderRadius: 8,
  }));

  return { nodes, edges };
}
