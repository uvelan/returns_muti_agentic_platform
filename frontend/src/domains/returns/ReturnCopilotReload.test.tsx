/**
 * Surviving a reload, and surviving the network.
 *
 * The Copilot used to hold a return entirely in component state: a minted
 * conversation id, a case id that only ever arrived on the turn that confirmed
 * the order, and everything derived from both. Reloading the browser -- or
 * having it reloaded for you, which is what a Windows update at a trade counter
 * does -- ended the return. The transcript was still on the server and the case
 * was still open; the only thing lost was the two strings that said which.
 *
 * These put those two strings in the URL and assert that each lifecycle stage
 * comes back from them, that nothing else is persisted, and that a reconnect
 * re-reads the case without re-running the agent.
 */

import { QueryClient, QueryClientProvider, onlineManager } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModuleNamespace from "../../api/cases";
import type * as OrderAgentModuleNamespace from "../../api/orderAgent";
import type { CaseProjection, ReturnRecordProjection } from "../../api/cases";
import type { AgentTurnResult } from "../../api/orderAgent";
import type { RuntimeConfig } from "../../api/runtimeConfig";
import { RuntimeConfigContext } from "../../hooks/useRuntimeConfig";
import { ReturnCopilotPage } from "./ReturnCopilotPage";

let conversationCounter = 0;

const mocks = vi.hoisted(() => ({
  sendTurn: vi.fn(),
  listConversations: vi.fn(),
  readTranscript: vi.fn(),
  can: vi.fn(),
  readCase: vi.fn(),
  listCases: vi.fn(),
  historyByOrder: vi.fn(),
  historyByCustomer: vi.fn(),
}));

vi.mock("../../api/orderAgent", async (importOriginal) => ({
  ...(await importOriginal<typeof OrderAgentModuleNamespace>()),
  orderAgentApi: {
    sendTurn: mocks.sendTurn,
    listConversations: mocks.listConversations,
    readTranscript: mocks.readTranscript,
  },
  newConversationId: () => `disc-${String(conversationCounter++)}`,
}));

// The transport is stubbed and nothing else is. `caseRefetchInterval` and
// `keepNewerRevision` are the behaviour under test here as much as in
// `api/cases.test.ts` -- a page wired to a stubbed predicate would pass these
// while polling on the wrong signal.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModuleNamespace>()),
  casesApi: { readProjection: mocks.readCase, list: mocks.listCases },
}));

vi.mock("../../api/returnHistory", () => ({
  returnHistoryApi: { byOrder: mocks.historyByOrder, byCustomer: mocks.historyByCustomer },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can, principal: { subject: "tester" } }),
}));

function runtimeConfig(): RuntimeConfig {
  return {
    releaseId: "release-under-test",
    environment: "test",
    apiBasePath: "/api",
    features: { orderDiscoveryCopilot: true },
    capabilities: { availableSourceTypes: [], availableModelProviders: [] },
    agents: { orderDiscovery: "order-discovery-agent" },
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <RuntimeConfigContext.Provider value={runtimeConfig()}>
        {children}
      </RuntimeConfigContext.Provider>
    </QueryClientProvider>
  );
}

/** Land the browser on a URL, the way a reload does. */
function reloadAt(query: string): void {
  window.history.replaceState(null, "", `/returns${query}`);
}

function currentParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

/**
 * A case read: the projection, which is the only shape the route serves.
 *
 * This fixture used to carry the legacy `CaseDetail` blocks *and* the lifecycle
 * envelope, because the panes read one and the polling read the other. Phase 7
 * bound the panes to the projection, so the left half is gone and not one
 * assertion in this file changed with it.
 */
function caseBody(
  lifecycle: Partial<CaseProjection> = {},
  returnRecords: ReturnRecordProjection[] = [],
): CaseProjection {
  return {
    caseId: "case-7",
    tenantId: "tenant-a",
    principalId: "tester",
    conversationId: "disc-old",
    status: "PROCESSING_RETURN",
    revision: 4,
    updatedAt: "2026-08-15T09:00:00Z",
    customer: null,
    confirmedOrder: {
      orderReference: "SO-A1",
      orderSource: null,
      sourceWebOrderNumber: null,
      trilogieOrderNumber: null,
      confirmationKey: null,
      candidateSetId: null,
      candidateId: null,
      confirmedAt: null,
    },
    selectedItems: null,
    facts: null,
    policyEvaluation: null,
    support: null,
    returnRecords,
    pickup: null,
    warehouse: null,
    settlement: null,
    stage: "ITEM_SELECTION",
    awaiting: ["POLICY"],
    businessComplete: false,
    isTerminal: false,
    ...lifecycle,
  };
}

/** The real record `4e372a39...`: an RMA issued, a label printed, no package. */
function rmaWithoutLabelOrTracking(): ReturnRecordProjection {
  return {
    returnRecordId: "4e372a39",
    returnReference: "RMA-OPS01-CD4364",
    status: "ISSUED",
    returnMethod: null,
    returnLocation: null,
    approvedItems: null,
    shipments: null,
    artifacts: null,
  };
}

function transcript(conversationId = "disc-old") {
  return {
    conversationId,
    conversationVersion: 7,
    messages: [
      { role: "associate", text: "melgon heating draft motor" },
      { role: "agent", text: "Order SO-A1." },
    ],
  };
}

function turn(overrides: Partial<AgentTurnResult> = {}): AgentTurnResult {
  return {
    conversation_id: "disc-0",
    conversation_version: 1,
    client_turn_id: "t-1",
    graph_generation_id: "gen-abc12345",
    model_provider: "MOCK",
    model_name: "scripted",
    query_evidence: [],
    response: { status: "OK", business_capability: "order-discovery", statements: [] },
    ...overrides,
  };
}

beforeEach(() => {
  reloadAt("");
  conversationCounter = 0;
  mocks.can.mockReturnValue(true);
  mocks.sendTurn.mockReset().mockResolvedValue(turn());
  mocks.listConversations.mockReset().mockResolvedValue([]);
  mocks.readTranscript.mockReset().mockResolvedValue(transcript());
  mocks.readCase.mockReset().mockResolvedValue(caseBody());
  mocks.listCases.mockReset().mockResolvedValue([]);
  mocks.historyByOrder
    .mockReset()
    .mockResolvedValue({ orderReference: null, accountId: null, customerId: null, cases: [] });
  mocks.historyByCustomer
    .mockReset()
    .mockResolvedValue({ orderReference: null, accountId: null, customerId: null, cases: [] });
});

afterEach(() => {
  onlineManager.setOnline(true);
  vi.useRealTimers();
  reloadAt("");
});

describe("identity lives in the URL", () => {
  it("puts the conversation in the address bar once there is one to come back to", async () => {
    // Not on mount. A minted id the associate has never spoken into has no
    // transcript on the server, so a reload that tried to restore it would ask
    // for a conversation that does not exist.
    render(<ReturnCopilotPage />, { wrapper: Wrapper });
    expect(currentParams().get("conversationId")).toBeNull();

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "melgon heating" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => {
      expect(currentParams().get("conversationId")).toBe("disc-0");
    });
  });

  it("puts the case in the address bar as soon as a turn raises one", async () => {
    mocks.sendTurn.mockResolvedValue(turn({ case_id: "case-7" }));
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "yes that one" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => {
      expect(currentParams().get("caseId")).toBe("case-7");
    });
  });

  it("persists nothing but the two ids", async () => {
    // The whole rule, asserted as a rule. A cached RMA or policy decision is a
    // copy of something the platform owns, and a copy outlives the fact: an
    // associate reloading after a cancellation would be shown the return as it
    // was.
    mocks.sendTurn.mockResolvedValue(turn({ case_id: "case-7" }));
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "yes that one" },
    });
    fireEvent.click(screen.getByLabelText("Send"));
    await waitFor(() => {
      expect(currentParams().get("caseId")).toBe("case-7");
    });

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    // The whole query string, not two `get` calls: a third parameter carrying
    // an order reference or an RMA would pass those and fail this.
    expect(window.location.search).toBe("?conversationId=disc-0&caseId=case-7");
  });

  it("takes both ids back out of the URL when a new return starts", async () => {
    reloadAt("?conversationId=disc-old&caseId=case-7");
    render(<ReturnCopilotPage />, { wrapper: Wrapper });
    await screen.findByText("Order SO-A1.");

    fireEvent.click(screen.getByRole("button", { name: /New return/ }));

    // Otherwise the next reload resumes the return the associate has just
    // walked away from.
    await waitFor(() => {
      expect(currentParams().get("conversationId")).toBeNull();
    });
    expect(currentParams().get("caseId")).toBeNull();
  });
});

describe("a reload at each stage of the lifecycle", () => {
  it("comes back to discovery with the transcript and no case", async () => {
    // A conversation that never confirmed an order has no case, and inventing
    // one to read would be the client-side join this whole path replaced.
    reloadAt("?conversationId=disc-old");
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    expect(await screen.findByText("Order SO-A1.")).toBeInTheDocument();
    expect(mocks.readTranscript).toHaveBeenCalledWith("disc-old");
    expect(mocks.readCase).not.toHaveBeenCalled();
  });

  it("comes back to item selection", async () => {
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(caseBody({ stage: "ITEM_SELECTION", awaiting: ["POLICY"] }));
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    expect(await screen.findByText("Order SO-A1.")).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    });
  });

  it("comes back to a case awaiting support", async () => {
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody({
        status: "AWAITING_SUPPORT",
        stage: "AWAITING_SUPPORT",
        awaiting: ["WARRANTY_VERIFICATION"],
      }),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    });
    // Support verifying a warranty claim is not a terminal route -- the case
    // rejoins the ordinary RMA lifecycle -- so the client stays on it.
    expect(await screen.findByText("Order SO-A1.")).toBeInTheDocument();
  });

  it("comes back to an RMA that has no label and no tracking", async () => {
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody(
        { stage: "AUTHORIZED_RMA", awaiting: ["TRACKING", "LABEL"] },
        [rmaWithoutLabelOrTracking()],
      ),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    // The RMA is back without the associate typing anything, which is the whole
    // point of putting the case id in the URL. It appears twice -- the progress
    // rail and the manifest -- and both are reading the same record.
    expect((await screen.findAllByText("RMA-OPS01-CD4364")).length).toBeGreaterThan(0);
    // And the tracking it does not have is pending, not invented.
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
  });

  it("takes the mode back from the stage, not from the conversation", async () => {
    // The whole of Gate 7 in one case: backend state, reload, same mode. The
    // transcript here says nothing about an RMA and there is no turn at all --
    // the pane is drawn from `stage` and the record the case carries.
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody({ stage: "AUTHORIZED_RMA", awaiting: ["TRACKING"] }, [rmaWithoutLabelOrTracking()]),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    expect(await screen.findByText("Authorized RMA")).toBeInTheDocument();
    expect(screen.getByText("Manifest & Label")).toBeInTheDocument();
  });

  it("comes back to each stage's own pane", async () => {
    for (const [stage, title] of [
      ["ITEM_SELECTION", "Item Selection"],
      ["POLICY_EVALUATION", "Evaluation"],
      ["CARRIER_TRANSIT", "Carrier Transit"],
      ["WAREHOUSE_RECEIVING", "Warehouse Dock"],
      ["COMPLETED", "Settlement"],
    ] as const) {
      reloadAt("?conversationId=disc-old&caseId=case-7");
      mocks.readCase.mockResolvedValue(caseBody({ stage }));
      const view = render(<ReturnCopilotPage />, { wrapper: Wrapper });
      expect(await screen.findByText(title)).toBeInTheDocument();
      view.unmount();
    }
  });

  it("does not let a model answer move the screen", async () => {
    // `turn.response.status === "APPROVED" | "ISSUED"` used to advance the
    // lifecycle and mint a policy decision. It is conversational metadata; the
    // case is the authority, and it still says item selection here.
    //
    // The capability is a real one from the agent policy rather than the
    // `POLICY_EVALUATION` this fixture used to send. That literal was a
    // `CopilotStage` wearing the capability field's name -- two vocabularies
    // that happen to share a spelling for evaluation, and the guard would
    // refuse it. `status` is the lever this test pulls; the capability only
    // has to be sayable.
    mocks.sendTurn.mockResolvedValue(
      turn({
        case_id: "case-7",
        response: {
          status: "APPROVED",
          business_capability: "return-context-collection",
          statements: [],
        },
      }),
    );
    mocks.readCase.mockResolvedValue(caseBody({ stage: "ITEM_SELECTION" }));
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "is it approved" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    });
    expect(await screen.findByText("Item Selection")).toBeInTheDocument();
    expect(screen.queryByText("Authorized RMA")).toBeNull();
    expect(screen.queryByText("Evaluation")).toBeNull();
  });

  it("does not re-run the agent to rebuild any of it", async () => {
    // A reload must cost two reads, not a model call. Re-issuing the last turn
    // would append a turn nobody typed and, on a confirmation, retry a write.
    reloadAt("?conversationId=disc-old&caseId=case-7");
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    });
    expect(mocks.sendTurn).not.toHaveBeenCalled();
  });

  it("reads the transcript once, not on every render", async () => {
    reloadAt("?conversationId=disc-old&caseId=case-7");
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await screen.findByText("Order SO-A1.");
    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "typing changes state" },
    });

    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalled();
    });
    expect(mocks.readTranscript).toHaveBeenCalledTimes(1);
  });
});

describe("polling, driven by the page", () => {
  it("keeps re-reading a case whose RMA has no tracking and no label", async () => {
    // The audit's defect, through the screen this time. The shipped predicate
    // was `returnRecords.length > 0`, so this render -- an RMA present, nothing
    // else -- was the last read the client ever performed on this case.
    vi.useFakeTimers();
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody(
        { stage: "AUTHORIZED_RMA", awaiting: ["TRACKING", "LABEL"], isTerminal: false },
        [rmaWithoutLabelOrTracking()],
      ),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.readCase).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(mocks.readCase).toHaveBeenCalledTimes(2);
  });

  it("stops re-reading a case that has become terminal", async () => {
    vi.useFakeTimers();
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody({
        status: "COMPLETED_EXTERNAL_SETTLEMENT",
        stage: "COMPLETED",
        awaiting: [],
        businessComplete: true,
        isTerminal: true,
      }),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.readCase).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.readCase).toHaveBeenCalledTimes(1);
  });

  it("keeps re-reading a rejected case's neighbours but not the rejected case", async () => {
    // `POLICY_REJECTED` is never business-complete and is nonetheless finished.
    // A client keyed on `businessComplete` would poll it for as long as the tab
    // stayed open.
    vi.useFakeTimers();
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody({
        status: "POLICY_REJECTED",
        stage: "COMPLETED",
        awaiting: ["POLICY"],
        businessComplete: false,
        isTerminal: true,
      }),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.readCase).toHaveBeenCalledTimes(1);
  });

  it("keeps re-reading a case awaiting recovery", async () => {
    vi.useFakeTimers();
    reloadAt("?conversationId=disc-old&caseId=case-7");
    mocks.readCase.mockResolvedValue(
      caseBody({
        status: "RECOVERY_REQUIRED",
        stage: "AUTHORIZED_RMA",
        awaiting: ["RECOVERY"],
        isTerminal: false,
      }),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    // Recovery restarts processing. Stopping here would go quiet across the
    // exact interval the case was being repaired in.
    expect(mocks.readCase).toHaveBeenCalledTimes(2);
  });
});

describe("coming back from an outage", () => {
  it("re-reads the case and does not re-issue the agent turn", async () => {
    mocks.sendTurn.mockResolvedValue(turn({ case_id: "case-7" }));
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "yes that one" },
    });
    fireEvent.click(screen.getByLabelText("Send"));
    await waitFor(() => {
      expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    });
    const readsBefore = mocks.readCase.mock.calls.length;

    // Through the manager rather than a `window` event: React Query only
    // notifies on a *change* of connectivity, and jsdom reports itself online
    // already, so dispatching "online" alone changes nothing.
    await act(() => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      return Promise.resolve();
    });

    await waitFor(() => {
      expect(mocks.readCase.mock.calls.length).toBeGreaterThan(readsBefore);
    });
    // The read recovers; the turn does not replay. Re-running it would spend a
    // model call and append a turn nobody typed -- and on a confirmation turn
    // it would be a second attempt at a write.
    expect(mocks.sendTurn).toHaveBeenCalledTimes(1);
  });
});

describe("resuming from the history list", () => {
  it("puts the resumed case's own order in the pane, not the last search", async () => {
    // Both halves of one rule. `setCandidates([])` used to run here and leave
    // the pane empty on a case the platform had already confirmed an order for;
    // removing it alone left the *previous* conversation's search results on
    // screen, which is a different return's order. The case is what says which
    // order this is, so the confirmed order replaces the stale list.
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-old", title: "earlier", messageCount: 2, updatedAt: null },
    ]);
    mocks.listCases.mockResolvedValue([
      {
        caseId: "case-7",
        status: "PROCESSING_RETURN",
        stage: "ITEM_SELECTION",
        isTerminal: false,
        confirmedOrderReference: "SO-A1",
        channelAConversationId: "disc-old",
        returnRecordCount: 0,
        updatedAt: "2026-08-12T00:00:00Z",
      },
    ]);
    mocks.sendTurn.mockResolvedValue(
      turn({
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "x",
            result: { candidates: [{ data: { sales_order_number: "SO-STALE" } }] },
          },
        ],
      }),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "melgon heating" },
    });
    fireEvent.click(screen.getByLabelText("Send"));
    expect(await screen.findByText("SO-STALE")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));
    await screen.findByText("Order SO-A1.");

    // The order comes back off the case, without the associate typing anything.
    expect((await screen.findAllByText("SO-A1")).length).toBeGreaterThan(0);
    expect(screen.queryByText("SO-STALE")).toBeNull();
  });

  it("opens an open return on the id the row is already holding", async () => {
    // The open-cases row draws `caseId` -- it is the React key -- and used to
    // hand `onOpen` only the conversation id, leaving the page to re-learn the
    // case with a second `casesApi.list(conversationId)` and take `.at(0)`.
    // That is a round trip for something in hand, and it fails quietly: one
    // empty answer and the case id was deleted from the URL, so a return the
    // platform had open came back with no return attached and a progress
    // stepper reading "Ready".
    mocks.listConversations.mockResolvedValue([]);
    mocks.listCases.mockResolvedValue([
      {
        caseId: "case-7",
        status: "PROCESSING_RETURN",
        stage: "ITEM_SELECTION",
        isTerminal: false,
        confirmedOrderReference: "SO-A1",
        channelAConversationId: "disc-old",
        returnRecordCount: 0,
        updatedAt: "2026-08-12T00:00:00Z",
      },
    ]);
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("SO-A1"));

    await waitFor(() => {
      expect(currentParams().get("caseId")).toBe("case-7");
    });
    expect(mocks.readCase).toHaveBeenCalledWith("case-7");
    // The only list read is the unnarrowed one that drew the panel. Nothing
    // asks the server which case this conversation raised.
    expect(mocks.listCases).not.toHaveBeenCalledWith("disc-old");
    // And the return arrives with its progress, which is the half of this the
    // associate sees: the confirmed order lights its own steps.
    expect(await screen.findByLabelText("Order selected: reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Case created: reached")).toBeInTheDocument();
  });

  it("still looks the case up for a past search, which does not know it", async () => {
    // The conversation-history rows are searches, most of which raised nothing.
    // There the lookup is the only way to find out, so it stays.
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-old", title: "earlier", messageCount: 2, updatedAt: null },
    ]);
    mocks.listCases.mockResolvedValue([]);
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    await waitFor(() => {
      expect(mocks.listCases).toHaveBeenCalledWith("disc-old");
    });
  });

  it("writes the resumed conversation and its case into the URL", async () => {
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-old", title: "earlier", messageCount: 2, updatedAt: null },
    ]);
    mocks.listCases.mockResolvedValue([
      {
        caseId: "case-7",
        status: "AWAITING_SUPPORT",
        confirmedOrderReference: "SO-A1",
        channelAConversationId: "disc-old",
        returnRecordCount: 1,
        updatedAt: "2026-08-12T00:00:00Z",
      },
    ]);
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    // So that a reload from here lands back on the same return.
    await waitFor(() => {
      expect(currentParams().get("caseId")).toBe("case-7");
    });
    expect(currentParams().get("conversationId")).toBe("disc-old");
  });
});
