import { fireEvent, render, screen, within } from "@testing-library/react";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DraftShapeView } from "../../api/graphSchema";
import { SchemaFlow } from "./SchemaFlow";

type TestNode = {
  id: string;
  data: { label: string };
};

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    ReactFlow: ({
      nodes,
      onNodeClick,
      children,
    }: {
      nodes: TestNode[];
      onNodeClick?: (event: ReactMouseEvent<HTMLButtonElement>, node: TestNode) => void;
      children: ReactNode;
    }) => (
      <div data-testid="flow-runtime">
        {nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            onClick={(event) => { onNodeClick?.(event, node); }}
          >
            Open {node.data.label}
          </button>
        ))}
        {children}
      </div>
    ),
    Background: () => null,
    Controls: () => null,
    Handle: () => null,
    MiniMap: () => null,
  };
});

const SHAPE = {
  entities: {
    Order: {
      label: "Order",
      source_dataset: "orders",
      properties: {
        order_id: {
          type: "STRING",
          source_field: "order_id",
          transformation: "NONE",
        },
        total: {
          type: "FLOAT",
          source_field: "line_items.amount",
          transformation: "SUM",
        },
      },
      identifier_properties: ["order_id"],
      ownership: "SOURCE",
      sync_mode: "INCREMENTAL",
    },
  },
  relationships: [
    {
      relationship_type: "PLACED_BY",
      from_label: "Order",
      to_label: "Customer",
      cardinality: "MANY_TO_ONE",
    },
  ],
  graph_indexes: [],
  graph_constraints: [],
} satisfies DraftShapeView;

class ResizeObserverAvailable {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

describe("SchemaFlow", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", ResizeObserverAvailable);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("exposes a named graph region and opens the selected entity inspector", () => {
    render(<SchemaFlow shape={SHAPE} />);

    expect(
      screen.getByRole("region", { name: "Schema relationship graph" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Order" }));

    const inspector = screen.getByText("Entity inspector").closest("aside");
    if (inspector === null) throw new Error("Entity inspector was not rendered");
    expect(within(inspector).getByRole("heading", { name: "Order" }))
      .toBeInTheDocument();
    expect(within(inspector).getByText("line_items.amount | SUM"))
      .toBeInTheDocument();
  });

  it("toggles to the complete semantic inventory with spoken direction", () => {
    render(<SchemaFlow shape={SHAPE} />);
    fireEvent.click(screen.getByRole("button", { name: "Details" }));

    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("from orders")).toBeInTheDocument();
    expect(screen.getByText("order_id (id)")).toBeInTheDocument();
    expect(screen.getByText("to")).toHaveClass("sr-only");
    expect(screen.getByText("PLACED_BY")).toBeInTheDocument();
    expect(screen.getByText("MANY_TO_ONE")).toBeInTheDocument();
  });
});
