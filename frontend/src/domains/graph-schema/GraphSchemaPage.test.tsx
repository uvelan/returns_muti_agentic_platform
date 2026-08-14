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

import type * as GraphSchemaModule from "../../api/graphSchema";
import { GraphSchemaPage } from "./GraphSchemaPage";

const mocks = vi.hoisted(() => ({
  listAnalyses: vi.fn(),
  getDraft: vi.fn(),
  getDraftShape: vi.fn(),
  listRevisions: vi.fn(),
  listClarifications: vi.fn(),
  validateDraft: vi.fn(),
  approveDraft: vi.fn(),
  publishDraft: vi.fn(),
  reanalyzeDraft: vi.fn(),
  applyMutations: vi.fn(),
  listBindings: vi.fn(),
  rebind: vi.fn(),
  clearBinding: vi.fn(),
  listReleases: vi.fn(),
  migrationPlan: vi.fn(),
  activateRelease: vi.fn(),
  can: vi.fn(),
}));

// Only the transport is stubbed. `TERMINAL_SESSION_STATUSES` mirrors the
// backend lifecycle table, and a mocked copy of it would be a second
// vocabulary to keep in step.
vi.mock("../../api/graphSchema", async (importOriginal) => ({
  ...(await importOriginal<typeof GraphSchemaModule>()),
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
    validateDraft: mocks.validateDraft,
    approveDraft: mocks.approveDraft,
    publishDraft: mocks.publishDraft,
    reanalyzeDraft: mocks.reanalyzeDraft,
    applyMutations: mocks.applyMutations,
  },
}));

vi.mock("../../api/sourceBindings", () => ({
  sourceBindingsApi: {
    list: mocks.listBindings,
    rebind: mocks.rebind,
    clear: mocks.clearBinding,
  },
}));

vi.mock("../../api/schemaReleases", () => ({
  schemaReleasesApi: {
    list: mocks.listReleases,
    migrationPlan: mocks.migrationPlan,
    activate: mocks.activateRelease,
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

/**
 * Publishing: the step that makes an approved schema the one the platform runs.
 *
 * Before it existed, approving a draft changed a document and the runtime went
 * on reading a file from the repository -- so these assert the order of the
 * gates, not the markup.
 */
describe("publishing a release", () => {
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
    mocks.validateDraft.mockResolvedValue({ passed: true, findings: [] });
    mocks.approveDraft.mockResolvedValue({ draft_id: "d1", status: "APPROVED" });
    mocks.publishDraft.mockResolvedValue({
      configurationReleaseId: "draft_d1_20260812000000",
      accepted: true,
      detail: "activated",
    });
  });

  async function openValidation() {
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Validation" }));
    await screen.findByRole("button", { name: "Validate" });
  }

  it("cannot publish before an approval", async () => {
    await openValidation();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => { expect(mocks.validateDraft).toHaveBeenCalled(); });

    // Validated is a shape someone might accept, not one to run. The backend
    // refuses either way; offering it would invite a 409 that reads as a bug.
    expect(screen.getByRole("button", { name: "Publish and activate" })).toBeDisabled();
  });

  it("publishes and activates only when asked to", async () => {
    await openValidation();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => { expect(mocks.validateDraft).toHaveBeenCalled(); });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => { expect(mocks.approveDraft).toHaveBeenCalled(); });

    fireEvent.click(screen.getByRole("button", { name: "Publish release" }));

    await waitFor(() => { expect(mocks.publishDraft).toHaveBeenCalledWith("d1", false); });
  });

  it("reports a refused compilation instead of claiming a release", async () => {
    mocks.publishDraft.mockResolvedValue({
      configurationReleaseId: "draft_d1_20260812000000",
      accepted: false,
      detail: "entity 'Order' has no identifier properties",
    });
    await openValidation();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => { expect(mocks.validateDraft).toHaveBeenCalled(); });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => { expect(mocks.approveDraft).toHaveBeenCalled(); });

    fireEvent.click(screen.getByRole("button", { name: "Publish and activate" }));

    // The element that was wrong, verbatim: it is the only part the analyst
    // can act on, and "publish failed" would send them back to the audit log.
    expect(await screen.findByText(/no identifier properties/)).toBeInTheDocument();
  });
});

/**
 * Where a dataset points, on the surface where someone asks.
 *
 * The panel's job is to make "configured" and "deliberately changed"
 * distinguishable, and to be honest that a rebinding does not move what is
 * already running.
 */
describe("source bindings", () => {
  const CONFIGURED = {
    dataset: "source_sales",
    sourceAssetId: "source_sales",
    connectorType: "MONGODB",
    connectionRef: "vault://data-sources/source-mongodb",
    objectRef: { database: "return_source", name: "salesInv" },
    incrementalCursorField: "source_updated_at",
    overridden: false,
  };

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
    mocks.listBindings.mockResolvedValue([CONFIGURED]);
    mocks.rebind.mockResolvedValue(undefined);
    mocks.clearBinding.mockResolvedValue(undefined);
  });

  async function openSources() {
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    await screen.findByText("source_sales");
  }

  it("says a change lands at the next publish, not now", async () => {
    // A rebinding that silently re-pointed a running release would make the
    // approval on it meaningless, so the panel does not imply it did.
    await openSources();

    expect(screen.getByText(/next publish/i)).toBeInTheDocument();
  });

  it("does not mark a configured binding as rebound", async () => {
    await openSources();

    expect(screen.queryByText("rebound")).toBeNull();
    // And there is nothing to reset when nothing was changed.
    expect(screen.queryByRole("button", { name: /Follow configuration/ })).toBeNull();
  });

  it("moves only the connection, keeping what the dataset points at", async () => {
    await openSources();
    fireEvent.click(screen.getByRole("button", { name: "Rebind" }));
    fireEvent.change(screen.getByLabelText("Connection for source_sales"), {
      target: { value: "vault://data-sources/restored" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rebind" }));

    await waitFor(() => { expect(mocks.rebind).toHaveBeenCalledTimes(1); });
    expect(mocks.rebind).toHaveBeenCalledWith("source_sales", {
      sourceAssetId: "source_sales",
      connectorType: "MONGODB",
      // Unchanged: pointing at a different object is pointing at different
      // data, which belongs with a schema change.
      objectRef: { database: "return_source", name: "salesInv" },
      connectionRef: "vault://data-sources/restored",
      incrementalCursorField: "source_updated_at",
    });
  });

  it("offers a way back to configuration once something is rebound", async () => {
    mocks.listBindings.mockResolvedValue([{ ...CONFIGURED, overridden: true }]);
    await openSources();

    expect(screen.getByText("rebound")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Follow configuration" }));

    await waitFor(() => { expect(mocks.clearBinding).toHaveBeenCalledWith("source_sales"); });
  });
});

/**
 * Re-analysis: what the source did while nobody was looking.
 *
 * The property worth defending here is that nothing is applied by running one.
 * A screen that helpfully accepted the proposal on the analyst's behalf would
 * be exactly the failure the backend refuses to allow, arriving through the UI
 * instead.
 */
describe("re-analysing a drifted source", () => {
  const FIELD_ADDED = {
    drift: "FIELD_ADDED",
    dataset: "orders",
    element: "Order.status",
    detail: "the source gained 'status' (declared 'string')",
    mutations: [
      {
        kind: "AddProperty",
        label: "Order",
        property_name: "status",
        property_type: "STRING",
        source_field: "orders.status",
      },
    ],
  };

  const NEEDS_A_HUMAN = {
    drift: "FIELD_REMOVED",
    dataset: "orders",
    element: "Order.order_id",
    detail: "the source no longer has the field Order identifies on.",
    mutations: [],
  };

  const PROPOSAL = {
    draft_id: "d1",
    from_content_hash: "aaaa1111",
    to_content_hash: "bbbb2222",
    changes: [FIELD_ADDED],
    rebindings: [],
    diff: {
      from_sequence: 3,
      to_sequence: 4,
      entries: [{ change_type: "MODIFIED", element: "Order", detail: "properties added: status" }],
    },
  };

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
    mocks.reanalyzeDraft.mockResolvedValue(PROPOSAL);
    mocks.applyMutations.mockResolvedValue({ draft_id: "d1", current_revision: 4 });
  });

  async function reanalyse() {
    await openAnalysis();
    fireEvent.click(screen.getByRole("tab", { name: "Drift" }));
    fireEvent.click(await screen.findByRole("button", { name: "Re-analyse sources" }));
    await waitFor(() => { expect(mocks.reanalyzeDraft).toHaveBeenCalledWith("d1"); });
  }

  it("proposes without applying anything", async () => {
    await reanalyse();

    expect(await screen.findByText(/Order\.status/)).toBeInTheDocument();
    // The one assertion this whole feature exists for.
    expect(mocks.applyMutations).not.toHaveBeenCalled();
  });

  it("accepts a change through the ordinary mutations call", async () => {
    // Not a bespoke "accept re-analysis" endpoint: a second write path into a
    // draft would make the revision history stop being one story.
    await reanalyse();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));

    await waitFor(() => {
      expect(mocks.applyMutations).toHaveBeenCalledWith("d1", FIELD_ADDED.mutations);
    });
  });

  it("shows a change no command can express as a question, not a button", async () => {
    // Where the analyzer declined to guess is exactly the part a human is for,
    // so it must not be hidden and must not be clickable.
    mocks.reanalyzeDraft.mockResolvedValue({ ...PROPOSAL, changes: [NEEDS_A_HUMAN] });
    await reanalyse();

    expect(await screen.findByText(/Needs your decision/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("sends a moved dataset to the Sources tab instead of proposing a reshaping", async () => {
    // Where salesInv lives is a binding. Reshaping a graph because a database
    // was restored is the failure that distinction exists to prevent.
    mocks.reanalyzeDraft.mockResolvedValue({
      ...PROPOSAL,
      changes: [],
      rebindings: [
        {
          dataset: "orders",
          from_source_id: "mongo_main",
          to_source_id: "restored",
          to_dataset: "orders_v2",
          detail: "the same fields now come from 'restored'",
        },
      ],
      diff: { from_sequence: 3, to_sequence: 4, entries: [] },
    });
    await reanalyse();

    expect(await screen.findByText(/Moved, not changed/)).toBeInTheDocument();
    expect(screen.getByText(/Sources tab/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("says a source that did not move did not move", async () => {
    mocks.reanalyzeDraft.mockResolvedValue({
      ...PROPOSAL,
      to_content_hash: PROPOSAL.from_content_hash,
      changes: [],
      rebindings: [],
      diff: { from_sequence: 3, to_sequence: 4, entries: [] },
    });
    await reanalyse();

    expect(await screen.findByText(/look the same as when this draft was designed/))
      .toBeInTheDocument();
  });

  it("does not offer to accept anything on read-only access", async () => {
    mocks.can.mockReturnValue(false);
    await reanalyse();

    expect(await screen.findByRole("button", { name: "Accept" })).toBeDisabled();
  });
});

/**
 * Releases: which schema the platform runs, and what changing it costs.
 *
 * Activation used to be a pointer flip with nothing to read first. These hold
 * the order that fixes it -- the plan is fetched and shown before the button
 * does anything -- and that a rebuild is never asserted without its reasons.
 */
describe("schema releases and migration plans", () => {
  const RELEASES = {
    activeReleaseId: "release_one",
    releases: [
      {
        configurationReleaseId: "release_one",
        configurationChecksum: "a".repeat(64),
        publishedBy: "analyst-1",
        publishedAt: "2026-08-12T00:00:00Z",
        active: true,
      },
      {
        configurationReleaseId: "release_two",
        configurationChecksum: "b".repeat(64),
        publishedBy: "analyst-1",
        publishedAt: "2026-08-12T01:00:00Z",
        active: false,
      },
    ],
  };

  const REBUILD_PLAN = {
    from_release_id: "release_one",
    to_release_id: "release_two",
    strategy: "FULL_REBUILD",
    node_labels_added: [],
    node_labels_removed: ["Customer"],
    node_labels_changed: [
      { element: "Order", detail: "identity changes from ['order_id'] to ['salesInvId']" },
    ],
    relationships_added: [],
    relationships_removed: [],
    relationships_changed: [],
    objects_to_create: [
      {
        kind: "NODE_KEY_CONSTRAINT",
        label: "Order",
        properties: ["graph_generation_id", "salesInvId"],
        detail: "unique",
      },
    ],
    objects_to_drop: [],
    rebuild_reasons: ["Order: identity changes, so a merge would insert a second node"],
  };

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
    mocks.listReleases.mockResolvedValue(RELEASES);
    mocks.migrationPlan.mockResolvedValue(REBUILD_PLAN);
    mocks.activateRelease.mockResolvedValue(REBUILD_PLAN);
  });

  async function openReleases() {
    render(<GraphSchemaPage />, { wrapper });
    fireEvent.click(await screen.findByRole("tab", { name: "Releases" }));
    return screen.findByRole("button", { name: /release_two/ });
  }

  it("is readable before an analysis is selected", async () => {
    // Which schema is live is a fact about the runtime, not about one draft.
    await openReleases();

    expect(screen.getByRole("button", { name: /release_one/ })).toBeInTheDocument();
  });

  it("plans before it activates", async () => {
    const target = await openReleases();
    fireEvent.click(target);

    await waitFor(() => { expect(mocks.migrationPlan).toHaveBeenCalledWith("release_two"); });
    expect(mocks.activateRelease).not.toHaveBeenCalled();
  });

  it("states a rebuild with the reason for it", async () => {
    // A rebuild verdict without a why is not something anyone can act on.
    const target = await openReleases();
    fireEvent.click(target);

    expect(await screen.findByText("FULL_REBUILD")).toBeInTheDocument();
    expect(screen.getByText(/insert a second node/)).toBeInTheDocument();
    expect(screen.getByText("Customer")).toBeInTheDocument();
  });

  it("activates only on the button, and reports the plan it recorded", async () => {
    const target = await openReleases();
    fireEvent.click(target);
    fireEvent.click(await screen.findByRole("button", { name: "Activate" }));

    await waitFor(() => { expect(mocks.activateRelease).toHaveBeenCalledWith("release_two"); });
    expect(await screen.findByText(/recorded against the release/)).toBeInTheDocument();
  });

  it("does not offer to activate the release that is already live", async () => {
    await openReleases();
    fireEvent.click(screen.getByRole("button", { name: /release_one/ }));

    expect(await screen.findByRole("button", { name: "Live" })).toBeDisabled();
  });

  it("shows the plan but not the button without the activate capability", async () => {
    mocks.can.mockReturnValue(false);
    const target = await openReleases();
    fireEvent.click(target);

    expect(await screen.findByText("FULL_REBUILD")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled();
  });

  it("says nothing is published rather than showing an empty list", async () => {
    // Every installation starts here, running the schema file it shipped with.
    mocks.listReleases.mockResolvedValue({ activeReleaseId: null, releases: [] });
    render(<GraphSchemaPage />, { wrapper });
    fireEvent.click(await screen.findByRole("tab", { name: "Releases" }));

    expect(await screen.findByText(/Nothing has been published yet/)).toBeInTheDocument();
  });
});
