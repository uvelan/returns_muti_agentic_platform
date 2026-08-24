import { describe, expect, it } from "vitest";
import type { Edge } from "@xyflow/react";
import { layout } from "./schemaLayout";

/**
 * Positions must come from the relationships.
 *
 * The backend sends `x`/`y` from `_canvas_position`, which places entities on a
 * square grid *by array index*. Nothing there reads a relationship, so two
 * connected entities were as likely to land at opposite corners as adjacent,
 * and every edge crossed the diagram. The renderer was never the problem.
 */

type TestNode = Parameters<typeof layout>[0][number];

function node(id: string): TestNode {
  return {
    id,
    type: "analyzerEntity",
    position: { x: 0, y: 0 },
    // `layout` reads ids and positions only; the payload rides along untouched.
    data: { name: id },
  };
}

function edge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target };
}

const columnsOf = (placed: readonly TestNode[]) =>
  [...new Set(placed.map((item) => item.position.x))].sort((a, b) => a - b);

describe("layout", () => {
  it("ranks a chain left to right, one column per step", () => {
    const placed = layout(
      [node("customer"), node("order"), node("line")],
      [edge("customer", "order"), edge("order", "line")],
    );
    const x: Record<string, number> = Object.fromEntries(
      placed.map((item) => [item.id, item.position.x]),
    );

    expect(x.customer).toBeLessThan(x.order);
    expect(x.order).toBeLessThan(x.line);
    expect(columnsOf(placed)).toHaveLength(3);
  });

  it("puts siblings of one parent in the same column", () => {
    const placed = layout(
      [node("order"), node("line"), node("shipment")],
      [edge("order", "line"), edge("order", "shipment")],
    );
    const x: Record<string, number> = Object.fromEntries(
      placed.map((item) => [item.id, item.position.x]),
    );

    expect(x.line).toBe(x.shipment);
    expect(x.order).toBeLessThan(x.line);
  });

  it("does not stack two entities on the same point", () => {
    const placed = layout(
      [node("order"), node("line"), node("shipment")],
      [edge("order", "line"), edge("order", "shipment")],
    );
    const points = new Set(placed.map((item) => `${String(item.position.x)},${String(item.position.y)}`));
    expect(points.size).toBe(placed.length);
  });

  it("survives a relationship naming an entity that is not in the schema", () => {
    // A half-applied change, or an existing graph beside a proposal that drops
    // an entity. Passing the edge to dagre anyway makes it invent an empty node.
    const placed = layout([node("order")], [edge("order", "deleted_entity")]);
    expect(placed).toHaveLength(1);
    expect(Number.isFinite(placed[0]?.position.x)).toBe(true);
  });

  it("places an entity with no relationships rather than dropping it", () => {
    const placed = layout(
      [node("order"), node("line"), node("orphan")],
      [edge("order", "line")],
    );
    expect(placed).toHaveLength(3);
    for (const item of placed) {
      expect(Number.isFinite(item.position.x)).toBe(true);
      expect(Number.isFinite(item.position.y)).toBe(true);
    }
  });

  it("is stable: the same graph lays out the same way twice", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const edges = [edge("a", "b"), edge("b", "c")];
    expect(layout(nodes, edges)).toEqual(layout(nodes, edges));
  });
});
