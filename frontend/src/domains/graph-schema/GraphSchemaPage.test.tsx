/**
 * The schema canvas, and the three tabs the draft shape backed.
 *
 * The column used to say, correctly, that it could not draw: the analyzer
 * served `entity_count` and `relationship_count` and nothing else. These tests
 * hold the properties that make the replacement worth more than the counts it
 * replaced.
 *
 * **Cardinality is rendered.** It is the half a count hides completely -- two
 * drafts with the same `relationship_count` can describe entirely different
 * graphs -- and it is the easiest thing for a later tidy-up to drop.
 *
 * **The shape is fetched once for the canvas and the tabs.** They are
 * projections of one payload sharing a query key, not three fetches.
 *
 * **An empty draft is empty, not broken.** The backend returns an empty shape
 * rather than 404 for exactly this, so the screen must not report an error.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GraphSchemaPage } from "./GraphSchemaPage";

const mocks = vi.hoisted(() => ({
  listAnalyses: vi.fn(),
  getDraft: vi.fn(),
  getDraftShape: vi.fn(),
  listRevisions: vi.fn(),
  listClarifications: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/graphSchema", () => ({
  graphSchemaApi: {
    listAnalyses: mocks.listAnalyses,
    getAnalysis: vi.fn(),
    createAnalysis: vi.fn(),
    abandonAnalysis: vi.fn(),
    getSnapshot: vi.fn(),
    listClarifications: mocks.listClarifications,
    answerClarification: vi.fn(),
    getDraft: mocks.getDraft,
    getDraftShape: mocks.getDraftShape,
    listRevisions: mocks.listRevisions,
    validateDraft: vi.fn(),
    approveDraft: vi.fn(),
    applyMutations: vi.fn(),
  },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

const ANALYSIS = {
  analysis_id: "a1",
  status: "PROPOSED",
  draft_id: "d1",
  source_refs: ["mongo_main"],
};

const SHAPE = {
  entities: {
    Order: {
      label: "Order",
      source_dataset: "orders",
      properties: {
        order_id: { type: "STRING", source_field: "order_id", transformation: "NONE" },
        total: { type: "FLOAT", source_field: null, transformation: "SUM" },
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
  graph_indexes: [{ label: "Order", properties: ["order_id"] }],
  graph_constraints: [
    { label: "Order", property_name: "order_id", unique: true, required: true },
  ],
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

async function openAnalysis() {
  render(<GraphSchemaPage />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /a1/ }));
}

describe("Graph schema canvas", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listAnalyses.mockResolvedValue([ANALYSIS]);
    mocks.getDraft.mockResolvedValue({
      draft_id: "d1",
      status: "DRAFT",
      current_revision: 3,
      entity_count: 1,
      relationship_count: 1,
    });
    mocks.getDraftShape.mockResolvedValue(SHAPE);
    mocks.listRevisions.mockResolvedValue([]);
    mocks.listClarifications.mockResolvedValue([]);
  });

  it("draws the entities the counts only counted", async () => {
    await openAnalysis();

    // `findAllBy`, not `findBy`: the canvas and the detail tabs are both on
    // screen, so an entity label legitimately appears more than once.
    expect(await screen.findAllByText("Order")).not.toHaveLength(0);
    expect(screen.getByText(/from orders/)).toBeInTheDocument();
  });

  it("marks which properties are identifiers", async () => {
    // The first thing a reviewer checks about a proposed entity.
    await openAnalysis();

    expect(await screen.findByText(/order_id \(id\)/)).toBeInTheDocument();
  });

  it("renders relationships with their cardinality", async () => {
    await openAnalysis();

    expect(await screen.findByText("MANY_TO_ONE")).toBeInTheDocument();
    expect(screen.getByText("PLACED_BY")).toBeInTheDocument();
  });

  it("says an empty draft is empty rather than failing", async () => {
    mocks.getDraftShape.mockResolvedValue({
      entities: {},
      relationships: [],
      graph_indexes: [],
      graph_constraints: [],
    });
    await openAnalysis();

    expect(await screen.findByText(/no entities yet/)).toBeInTheDocument();
  });
});

describe("Graph schema tabs backed by the shape", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listAnalyses.mockResolvedValue([ANALYSIS]);
    mocks.getDraft.mockResolvedValue({
      draft_id: "d1",
      status: "DRAFT",
      current_revision: 3,
      entity_count: 1,
      relationship_count: 1,
    });
    mocks.getDraftShape.mockResolvedValue(SHAPE);
    mocks.listRevisions.mockResolvedValue([]);
    mocks.listClarifications.mockResolvedValue([]);
  });

  it("shows property types on the Properties tab", async () => {
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Properties" }));

    expect(await screen.findAllByText("FLOAT")).not.toHaveLength(0);
  });

  it("shows an unmapped property as unmapped rather than blank", async () => {
    // A derived field is a real state; blank would read as missing data.
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Mapping" }));

    expect(await screen.findByText("unmapped")).toBeInTheDocument();
    expect(screen.getByText("SUM")).toBeInTheDocument();
  });

  it("shows indexes and constraints on the Indexes tab", async () => {
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Indexes" }));

    expect(await screen.findAllByText(/order_id/)).not.toHaveLength(0);
    expect(screen.getByText(/unique/)).toBeInTheDocument();
  });

  it("still says Sync is out of scope rather than inventing it", async () => {
    // Not an unwired tab: build and activation are generation-lifecycle
    // operations on a different surface.
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Sync" }));

    expect(await screen.findByText(/lifecycle operations outside this surface/)).toBeInTheDocument();
  });

  it("fetches the shape once for the canvas and the tabs", async () => {
    // One payload, several projections, sharing a query key. Three fetches for
    // three views of the same document would be the easy mistake.
    await openAnalysis();
    await screen.findAllByText("Order");
    fireEvent.click(screen.getByRole("tab", { name: "Properties" }));
    await screen.findAllByText("FLOAT");

    await waitFor(() => {
      expect(mocks.getDraftShape).toHaveBeenCalledTimes(1);
    });
  });
});
