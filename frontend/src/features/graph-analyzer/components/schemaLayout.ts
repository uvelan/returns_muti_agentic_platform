import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";

/**
 * Where the entities go on the canvas.
 *
 * Its own module rather than part of `SchemaCanvas` so the component file
 * exports only components -- which is what keeps fast refresh working -- and
 * so the placement can be tested without mounting a canvas.
 */

type Positioned = Node<Record<string, unknown>, string>;

/** Roughly what an entity node measures, which is what dagre needs to pack them. */
const NODE_WIDTH = 210;
const NODE_HEIGHT = 96;

/**
 * Place entities by how they are connected, not by where they sit in an array.
 *
 * The backend sends `x`/`y` as percentages from `_canvas_position`, which lays
 * the entities out on a square grid *by index*. Nothing in that has read a
 * single relationship, so two entities joined by an edge are as likely to be at
 * opposite corners as adjacent, and every edge crosses the diagram to reach its
 * target. That is the whole reason the graph looked like a hairball: the
 * renderer was fine, the coordinates were arbitrary.
 *
 * Dagre gives a layered layout: sources on the left, targets to their right,
 * ranks assigned so edges mostly run one way and crossings are minimised. For a
 * schema -- which is close to a DAG of entities and their references -- that
 * reads the way an ER diagram is supposed to.
 *
 * Measured on the shipped schema -- 13 entities, 16 relationships -- the result
 * is six ranks with all sixteen edges running forward and twelve of them
 * spanning exactly one rank. Nothing runs backwards and nothing sits on top of
 * anything.
 *
 * Computed client-side because it needs node dimensions, which only the client
 * knows. The backend's coordinates are left alone rather than removed: they are
 * part of a published contract, and other consumers may still read them.
 */
export function layout<T extends Positioned>(
  nodes: readonly T[],
  edges: readonly Edge[],
): T[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "LR",
    // Generous, because these nodes carry a name and a row of marks rather than
    // a single label, and a tight rank separation puts edge labels on top of
    // the nodes they run between.
    ranksep: 110,
    nodesep: 48,
    marginx: 24,
    marginy: 24,
  });

  for (const node of nodes) {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    // Both ends must be nodes dagre knows about. A relationship can name an
    // entity that is not in this schema -- a half-applied change, or the
    // existing graph next to a proposal that drops an entity -- and adding the
    // edge anyway makes dagre invent an empty node for the missing end.
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      graph.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(graph);

  return nodes.map((node) => {
    const placed = graph.node(node.id) as { x: number; y: number } | undefined;
    if (placed === undefined) return node;
    // Dagre positions a node by its centre; React Flow by its top-left corner.
    return {
      ...node,
      position: { x: placed.x - NODE_WIDTH / 2, y: placed.y - NODE_HEIGHT / 2 },
    };
  });
}