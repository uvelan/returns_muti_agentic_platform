/**
 * UI-04 -- what the case operations view says, and what it refuses to make up.
 *
 * The failure this screen exists to avoid is a plausible placeholder. Three
 * answers an operator wants are not published by any API -- live workflow
 * execution state, the workflow's business-calendar deadline, and a failure or
 * blocker code -- and a screen that filled those with "HEALTHY", "--" or a
 * relabelled support SLA would be trusted and wrong. So the tests below assert
 * the *absence* claims as hard as the presence ones.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModule from "../../api/cases";
import type { CaseDetail, CaseFact, CaseSummary } from "../../api/cases";
import type * as ConfigModule from "../../api/configuration";
import type { ReleaseAdoptionState } from "../../api/configuration";
import type { SupportMessage, SupportWorkItem } from "../../api/support";
import { CaseOperationsPage } from "./CaseOperationsPage";

const mocks = vi.hoisted(() => ({
  listCases: vi.fn(),
  readCase: vi.fn(),
  adoption: vi.fn(),
  readWorkItem: vi.fn(),
  listMessages: vi.fn(),
  can: vi.fn(),
}));

// The fact projection stays real: `latestFacts` is what decides which bay
// recommendation and which fulfilment status this screen shows.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModule>()),
  casesApi: { list: mocks.listCases, read: mocks.readCase },
}));

vi.mock("../../api/configuration", async (importOriginal) => ({
  ...(await importOriginal<typeof ConfigModule>()),
  configApi: { adoption: mocks.adoption },
}));

vi.mock("../../api/support", () => ({
  supportApi: { readWorkItem: mocks.readWorkItem, listMessages: mocks.listMessages },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function summary(overrides: Partial<CaseSummary> = {}): CaseSummary {
  return {
    caseId: "case-1",
    status: "AWAITING_SUPPORT",
    confirmedOrderReference: "CW273354",
    channelAConversationId: "disc-1",
    returnRecordCount: 1,
    updatedAt: "2026-08-13T10:00:00Z",
    ...overrides,
  };
}

function fact(name: string, value: unknown, overrides: Partial<CaseFact> = {}): CaseFact {
  return {
    factId: `${name}-1`,
    caseId: "case-1",
    factName: name,
    value,
    agentId: "order-discovery-agent",
    channel: "CHANNEL_A",
    acquisitionMethod: "STATED",
    turnId: null,
    sourceSystem: null,
    sourcePath: "ORDER_CONFIRMATION",
    observedAt: "2026-08-13T10:00:00Z",
    recordedAt: "2026-08-13T10:00:00Z",
    supersedesFactId: null,
    correlationId: null,
    ...overrides,
  };
}

function detail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  return {
    case: {
      caseId: "case-1",
      tenantId: "default",
      principalId: "dev-operator",
      branchId: null,
      status: "AWAITING_SUPPORT",
      channelAConversationId: "disc-1",
      channelBWorkItemId: null,
      confirmedOrderReference: "CW273354",
      confirmationKey: "default|disc-1|CW273354|1",
      sessionId: null,
      workflowId: "return-case-case-1",
      configurationReleaseId: "release-A",
      graphGenerationId: "gen-7",
      version: 2,
      createdAt: "2026-08-13T09:00:00Z",
      updatedAt: "2026-08-13T10:00:00Z",
    },
    returnRecords: [],
    unassignedItems: [],
    facts: [fact("confirmed_order_reference", "CW273354")],
    ...overrides,
  };
}

function adoptionState(overrides: Partial<ReleaseAdoptionState> = {}): ReleaseAdoptionState {
  return {
    status: "ACTIVATING",
    activated_release_id: "release-B",
    activated_head_revision: 2,
    pending_process_classes: ["api", "return-workflow-worker"],
    process_classes: [
      {
        process_class: "api",
        required: true,
        adopted: false,
        live_instances: 1,
        adopted_instances: 0,
        instances: [],
      },
      {
        process_class: "return-workflow-worker",
        required: true,
        adopted: false,
        live_instances: 0,
        adopted_instances: 0,
        instances: [],
      },
    ],
    evaluated_at: "2026-08-14T04:00:00Z",
    ...overrides,
  };
}

const WORK_ITEM: SupportWorkItem = {
  id: "wi-1",
  sessionId: null,
  caseId: "case-1",
  threadId: "th-1",
  status: "NEW",
  priority: "NORMAL",
  queue: "RETURNS",
  subject: "Return for CW273354",
  assignedTo: null,
  returnReference: null,
  shippingInstructionReference: null,
  slaDueAt: "2026-08-15T12:00:00Z",
  version: 1,
  createdAt: "2026-08-13T10:00:00Z",
  updatedAt: "2026-08-13T10:00:00Z",
};

function reminder(id: string, createdAt: string): SupportMessage {
  return {
    id,
    threadId: "th-1",
    sequence: 2,
    senderRole: "AGENT",
    senderId: "return-case-workflow",
    messageType: "REMINDER",
    messageText: "Still waiting on the RMA.",
    businessPayload: { reminderKey: "reminder-1" },
    createdAt,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<CaseOperationsPage />, { wrapper });
}

async function openCase() {
  renderPage();
  fireEvent.click(await screen.findByText("CW273354"));
  await screen.findByText("Execution");
}

describe("CaseOperationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listCases.mockResolvedValue([summary()]);
    mocks.readCase.mockResolvedValue(detail());
    mocks.adoption.mockResolvedValue(adoptionState());
    mocks.readWorkItem.mockResolvedValue(WORK_ITEM);
    mocks.listMessages.mockResolvedValue([]);
  });

  describe("what it will not invent", () => {
    it("says live execution state is not published rather than showing one", async () => {
      await openCase();

      const execution = enclosingPanel(await screen.findByText("Execution"));
      expect(within(execution).getAllByText(/not published by any API/i)).toHaveLength(2);
      expect(within(execution).getByText(/execution_state query/i)).toBeTruthy();
      // The workflow id is a different claim, and it is one the backend does make.
      expect(within(execution).getByText("return-case-case-1")).toBeTruthy();
    });

    it("says no failure or blocker field exists", async () => {
      await openCase();

      expect(await screen.findByText(/Failure or blocker/i)).toBeTruthy();
    });

    it("labels the support SLA as the support SLA, not as the workflow deadline", async () => {
      mocks.readCase.mockResolvedValue(
        detail({ case: { ...detail().case, channelBWorkItemId: "wi-1" } }),
      );
      await openCase();

      expect(await screen.findByText("Support SLA due")).toBeTruthy();
      expect(screen.getByText(/ReturnCaseTimings/)).toBeTruthy();
      expect(screen.queryByText(/^Deadline$/)).toBeNull();
    });

    it("reports a case with no support request as having no deadline at all", async () => {
      await openCase();

      expect(await screen.findByText(/no support deadline/i)).toBeTruthy();
      expect(mocks.readWorkItem).not.toHaveBeenCalled();
    });
  });

  describe("execution", () => {
    it("raises a case with no durable workflow as a fault", async () => {
      mocks.readCase.mockResolvedValue(detail({ case: { ...detail().case, workflowId: null } }));
      await openCase();

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(/No durable workflow is recorded/i);
      expect(alert).toHaveTextContent(/no deadline will fire/i);
    });
  });

  describe("release adoption", () => {
    it("names the process classes it is waiting on", async () => {
      await openCase();

      expect(await screen.findByText("ACTIVATING")).toBeTruthy();
      // Named, not counted -- "2 of 2 pending" tells an operator nothing they
      // can act on.
      expect(screen.getAllByText("api").length).toBeGreaterThan(0);
      expect(screen.getAllByText("return-workflow-worker").length).toBeGreaterThan(0);
    });

    it("tells a class that is not deployed from one that is behind", async () => {
      await openCase();

      // `api` has one live instance on the wrong revision; the worker has none.
      // Opposite fixes, so they must not share a badge.
      expect(await screen.findByText("deployed, behind")).toBeTruthy();
      expect(screen.getByText("not deployed")).toBeTruthy();
    });

    it("says the case ran under a different release from the activated one", async () => {
      await openCase();

      expect(await screen.findByText(/ran under a different release/i)).toBeTruthy();
    });

    it("reports adoption as unavailable rather than as adopted when the route fails", async () => {
      mocks.adoption.mockRejectedValue(new Error("Process adoption reporting is unavailable"));
      await openCase();

      expect(
        await screen.findByText(/Adoption reporting is unavailable/i),
      ).toBeTruthy();
      // The reassuring lie: an empty adoption state rendered as LIVE.
      expect(screen.queryByText("LIVE")).toBeNull();
    });
  });

  describe("reminders", () => {
    it("counts what actually reached Support", async () => {
      mocks.readCase.mockResolvedValue(
        detail({ case: { ...detail().case, channelBWorkItemId: "wi-1" } }),
      );
      mocks.listMessages.mockResolvedValue([
        reminder("m-1", "2026-08-13T11:00:00Z"),
        reminder("m-2", "2026-08-13T12:00:00Z"),
        { ...reminder("m-3", "2026-08-13T13:00:00Z"), businessPayload: {} },
      ]);
      await openCase();

      const channelB = enclosingPanel(await screen.findByText("Channel B"));
      // The third message carries no `reminderKey`, so it is an ordinary reply.
      expect(within(channelB).getByText("Reminders sent").nextSibling).toHaveTextContent("2");
      expect(within(channelB).getByText("2026-08-13T12:00:00Z")).toBeTruthy();
    });
  });

  describe("permitted interventions", () => {
    it("names the route and the missing grant rather than hiding the act", async () => {
      mocks.can.mockImplementation((capability: string) => capability !== "returns.logistics.act");
      await openCase();

      const panel = enclosingPanel(await screen.findByText("Permitted interventions"));
      expect(within(panel).getByText("POST /api/return-shipments/{rma}/updates")).toBeTruthy();
      expect(within(panel).getByText(/Requires returns\.logistics\.act/)).toBeTruthy();
    });

    it("says why an act this case's state forbids is unavailable", async () => {
      await openCase();

      const panel = enclosingPanel(await screen.findByText("Permitted interventions"));
      expect(within(panel).getByText(/No support request has been raised/i)).toBeTruthy();
      expect(within(panel).getByText(/No RMA on this case has a reference/i)).toBeTruthy();
    });
  });

  describe("the case history", () => {
    it("keeps a superseded fact in the log while the panels show the newest", async () => {
      mocks.readCase.mockResolvedValue(
        detail({
          facts: [
            fact("bay_reference", "BAY-OLD"),
            fact("bay_reference", "BAY-NEW", {
              factId: "bay-2",
              recordedAt: "2026-08-13T11:00:00Z",
              supersedesFactId: "bay_reference-1",
            }),
          ],
        }),
      );
      await openCase();

      // The panel projects newest-per-name...
      const state = enclosingPanel(await screen.findByRole("heading", { name: "Case" }));
      expect(within(state).getByText("BAY-NEW")).toBeTruthy();
      expect(within(state).queryByText("BAY-OLD")).toBeNull();
      // ...and the log keeps both, because an auditor asks what was believed then.
      const history = enclosingPanel(screen.getByText(/Case history \(2\)/));
      expect(within(history).getByText("BAY-OLD")).toBeTruthy();
      expect(within(history).getByText("supersedes earlier")).toBeTruthy();
    });
  });

  describe("access", () => {
    it("asks for nothing without returns.session.read", () => {
      mocks.can.mockReturnValue(false);
      renderPage();

      expect(screen.getByText(/requires returns\.session\.read/i)).toBeTruthy();
      expect(mocks.listCases).not.toHaveBeenCalled();
    });

    it("distinguishes an empty case list from a failed one", async () => {
      mocks.listCases.mockRejectedValue(new Error("The platform store is unavailable."));
      renderPage();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "The platform store is unavailable.",
      );
      expect(screen.queryByText("No cases yet.")).toBeNull();
    });
  });
});

/** The `<section>` a panel heading sits in. */
function enclosingPanel(heading: HTMLElement): HTMLElement {
  const found = heading.closest("section");
  if (!(found instanceof HTMLElement)) throw new Error(`no panel around ${heading.textContent ?? ""}`);
  return found;
}
