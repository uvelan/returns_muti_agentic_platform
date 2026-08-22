/**
 * UI-04 -- what the case operations view says, and what it refuses to make up.
 *
 * The failure this screen exists to avoid is a plausible placeholder. Answers an
 * operator wants that no API publishes -- live workflow execution state, the
 * workflow's business-calendar deadline, a failure or blocker code -- would be
 * trusted and wrong if filled with "HEALTHY", "--" or a relabelled support SLA.
 * So the tests below assert the *absence* claims as hard as the presence ones.
 *
 * **The screen now reads `CaseProjection`**, and that moved several answers from
 * one category to the other. `workflowId`, `configurationReleaseId` and
 * `graphGenerationId` are no longer published on this resource, and the facts
 * arrive as the latest-per-name projection rather than the append-only log. Each
 * of those is now an absence claim, and each is tested as one: what must never
 * happen is the panel quietly rendering less under the old heading.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModule from "../../api/cases";
import type { CaseFactProjection, CaseProjection, CaseSummary } from "../../api/cases";
import type * as ConfigModule from "../../api/configuration";
import type { ReleaseAdoptionState } from "../../api/configuration";
import type { SupportMessage, SupportWorkItem } from "../../api/support";
import { CaseOperationsPage } from "./CaseOperationsPage";
import { formatTimestamp } from "../../format/datetime";

const mocks = vi.hoisted(() => ({
  listCases: vi.fn(),
  readCase: vi.fn(),
  adoption: vi.fn(),
  readWorkItem: vi.fn(),
  listMessages: vi.fn(),
  can: vi.fn(),
}));

// Only the transport is stubbed. `bayRecommendation` and `projectedFactString`
// are the real ones: they decide which bay and which fulfilment status this
// screen shows, and a stub of them would test the fixture.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModule>()),
  casesApi: { list: mocks.listCases, readProjection: mocks.readCase },
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
    stage: "AWAITING_SUPPORT",
    isTerminal: false,
    confirmedOrderReference: "CW273354",
    channelAConversationId: "disc-1",
    returnRecordCount: 1,
    updatedAt: "2026-08-13T10:00:00Z",
    ...overrides,
  };
}

function fact(
  name: string,
  value: string | number | boolean | null,
  overrides: Partial<CaseFactProjection> = {},
): CaseFactProjection {
  return {
    factId: `${name}-1`,
    factName: name,
    value,
    agentId: "order-discovery-agent",
    channel: "CHANNEL_A",
    acquisitionMethod: "STATED",
    sourceSystem: "RETURN_PLATFORM",
    observedAt: "2026-08-13T10:00:00Z",
    recordedAt: "2026-08-13T10:00:00Z",
    supersedesFactId: null,
    ...overrides,
  };
}

function projection(overrides: Partial<CaseProjection> = {}): CaseProjection {
  return {
    caseId: "case-1",
    tenantId: "default",
    principalId: "dev-operator",
    conversationId: "disc-1",
    status: "AWAITING_SUPPORT",
    revision: 2,
    updatedAt: "2026-08-13T10:00:00Z",
    customer: null,
    confirmedOrder: {
      orderReference: "CW273354",
      orderSource: null,
      sourceWebOrderNumber: null,
      trilogieOrderNumber: null,
      confirmationKey: "default|disc-1|CW273354|1",
      candidateSetId: null,
      candidateId: null,
      confirmedAt: "2026-08-13T09:00:00Z",
    },
    selectedItems: null,
    facts: [fact("confirmed_order_reference", "CW273354")],
    policyEvaluation: null,
    support: null,
    returnRecords: null,
    pickup: null,
    warehouse: null,
    settlement: { status: "NOT_INTEGRATED", creditMemoReference: null, settledAmount: null, settledAt: null },
    stage: "AWAITING_SUPPORT",
    awaiting: ["POLICY", "RETURN_METHOD"],
    businessComplete: false,
    isTerminal: false,
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

/** The `support` block, which is where the Channel B link lives on the projection. */
function supportBlock(): NonNullable<CaseProjection["support"]> {
  return {
    workItemId: "wi-1",
    threadId: "th-1",
    queue: "RETURNS",
    status: "NEW",
    subject: "Return for CW273354",
    priority: "NORMAL",
    assignedTo: null,
    slaDueAt: "2026-08-15T12:00:00Z",
    openedAt: "2026-08-13T10:00:00Z",
    resolvedAt: null,
  };
}

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
  await screen.findByText("Lifecycle");
}

describe("CaseOperationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listCases.mockResolvedValue([summary()]);
    mocks.readCase.mockResolvedValue(projection());
    mocks.adoption.mockResolvedValue(adoptionState());
    mocks.readWorkItem.mockResolvedValue(WORK_ITEM);
    mocks.listMessages.mockResolvedValue([]);
  });

  describe("what it will not invent", () => {
    it("says live execution state is not published rather than showing one", async () => {
      await openCase();

      const lifecycle = enclosingPanel(await screen.findByText("Lifecycle"));
      expect(within(lifecycle).getAllByText(/not published by any API/i)).toHaveLength(3);
      expect(within(lifecycle).getByText(/execution_state query/i)).toBeTruthy();
    });

    it("says the durable execution is not on this resource rather than implying one", async () => {
      // `workflowId` used to be shown here and is not on `CaseProjection`. The
      // panel must say that, not fall silent -- an operator who saw no workflow
      // line would read it as no workflow.
      await openCase();

      const lifecycle = enclosingPanel(await screen.findByText("Lifecycle"));
      expect(within(lifecycle).getByText(/Durable execution/i)).toBeTruthy();
      expect(within(lifecycle).getByText(/no workflowId or sessionId/i)).toBeTruthy();
    });

    it("says no failure or blocker field exists", async () => {
      await openCase();

      expect(await screen.findByText(/Failure or blocker/i)).toBeTruthy();
    });

    it("labels the support SLA as the support SLA, not as the workflow deadline", async () => {
      mocks.readCase.mockResolvedValue(projection({ support: supportBlock() }));
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

  describe("lifecycle", () => {
    it("shows what the platform says the case is waiting for", async () => {
      await openCase();

      const lifecycle = enclosingPanel(await screen.findByText("Lifecycle"));
      // Rendered from `awaiting`, which the backend computes from the release's
      // return-method requirement table. Nothing here derives it.
      expect(within(lifecycle).getByText("POLICY, RETURN_METHOD")).toBeTruthy();
      expect(within(lifecycle).getByText("Stage").nextSibling).toHaveTextContent(
        "AWAITING_SUPPORT",
      );
    });

    it("raises a case waiting on nothing that is neither complete nor finished", async () => {
      // The state the missing-`workflowId` alert used to approximate: nothing
      // outstanding, nothing done, nothing finished. Said from a computation now
      // rather than inferred from a null column.
      mocks.readCase.mockResolvedValue(
        projection({ awaiting: [], businessComplete: false, isTerminal: false }),
      );
      await openCase();

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(/waiting on nothing/i);
      expect(alert).toHaveTextContent(/Nothing will move it/i);
    });

    it("does not raise a completed case as a fault", async () => {
      mocks.readCase.mockResolvedValue(
        projection({
          status: "COMPLETED_EXTERNAL_SETTLEMENT",
          stage: "COMPLETED",
          awaiting: [],
          businessComplete: true,
          isTerminal: true,
        }),
      );
      await openCase();

      expect(screen.queryByRole("alert")).toBeNull();
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

    it("says the case's own release is not published rather than comparing against nothing", async () => {
      // It used to say "this case ran under a different release". The projection
      // carries no `configurationReleaseId`, so the comparison is gone -- and
      // silently dropping it would leave adoption looking case-scoped when it is
      // platform-wide.
      await openCase();

      const release = enclosingPanel(
        await screen.findByText("Configuration release and adoption"),
      );
      expect(within(release).getByText(/The release this case ran under/i)).toBeTruthy();
      expect(within(release).getByText(/Adoption below is platform-wide/i)).toBeTruthy();
      expect(screen.queryByText(/ran under a different release/i)).toBeNull();
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

  describe("bay and facts", () => {
    it("shows the bay the workflow recorded, read off the projected facts", async () => {
      mocks.readCase.mockResolvedValue(
        projection({
          facts: [
            fact("bay_warehouse_reference", "WH-01"),
            fact("bay_reference", "BAY-14"),
            fact("bay_return_location", "DOCK-3"),
          ],
        }),
      );
      await openCase();

      const state = enclosingPanel(await screen.findByRole("heading", { name: "Case" }));
      expect(within(state).getByText("WH-01 / BAY-14 / DOCK-3")).toBeTruthy();
    });

    it("reports no bay as a state rather than a fault, with the reason the engine gave", async () => {
      mocks.readCase.mockResolvedValue(
        projection({ facts: [fact("bay_reason", "PRE_ARRIVAL_NOT_ALLOWED")] }),
      );
      await openCase();

      expect(await screen.findByText(/No bay recommended/i)).toHaveTextContent(
        /PRE_ARRIVAL_NOT_ALLOWED/,
      );
      // Not an alert. Placement is best-effort by declared policy.
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("says the facts are the latest per name and not the log", async () => {
      // The projection serves `latest_case_facts`, so a superseded value is no
      // longer here. The heading and note have to say that: the same shorter
      // list under "Case history" would read as an audit trail with entries
      // missing.
      mocks.readCase.mockResolvedValue(
        projection({
          facts: [
            fact("bay_reference", "BAY-NEW", {
              factId: "bay-2",
              recordedAt: "2026-08-13T11:00:00Z",
              supersedesFactId: "bay_reference-1",
            }),
          ],
        }),
      );
      await openCase();

      const facts = enclosingPanel(await screen.findByText(/Case facts \(1\)/));
      expect(within(facts).getByText(/Not the append-only log/i)).toBeTruthy();
      expect(within(facts).getByText("BAY-NEW")).toBeTruthy();
      // The correction is still legible as a correction.
      expect(within(facts).getByText("supersedes earlier")).toBeTruthy();
      expect(screen.queryByText(/Case history/)).toBeNull();
    });
  });

  describe("RMAs", () => {
    it("shows a label with no package as exactly that", async () => {
      // Record `4e372a39...`. Two fields where one is filled and one is blank
      // is what an operator has to interpret; this says it.
      mocks.readCase.mockResolvedValue(
        projection({
          returnRecords: [
            {
              returnRecordId: "4e372a39",
              returnReference: "RMA-OPS01-CD4364",
              status: "ISSUED",
              returnMethod: "PREPAID_PARCEL",
              returnLocation: null,
              approvedItems: null,
              shipments: null,
              artifacts: [
                {
                  artifactId: "LBL-OPS01",
                  artifactType: "SHIPPING_LABEL",
                  shipmentId: null,
                  fileName: null,
                  mediaType: null,
                  version: 1,
                  active: true,
                  supersededBy: null,
                  expiresAt: null,
                  createdAt: null,
                },
              ],
            },
          ],
        }),
      );
      await openCase();

      const rmas = enclosingPanel(await screen.findByText("RMAs (1)"));
      expect(within(rmas).getByText("RMA-OPS01-CD4364")).toBeTruthy();
      expect(within(rmas).getByText("LBL-OPS01")).toBeTruthy();
      expect(within(rmas).getByText(/no package has been tendered/i)).toBeTruthy();
    });
  });

  describe("reminders", () => {
    it("counts what actually reached Support", async () => {
      mocks.readCase.mockResolvedValue(projection({ support: supportBlock() }));
      mocks.listMessages.mockResolvedValue([
        reminder("m-1", "2026-08-13T11:00:00Z"),
        reminder("m-2", "2026-08-13T12:00:00Z"),
        { ...reminder("m-3", "2026-08-13T13:00:00Z"), businessPayload: {} },
      ]);
      await openCase();

      const channelB = enclosingPanel(await screen.findByText("Channel B"));
      // The third message carries no `reminderKey`, so it is an ordinary reply.
      expect(within(channelB).getByText("Reminders sent").nextSibling).toHaveTextContent("2");
      // The formatted value, because the raw ISO string is what this screen used
      // to show and it named no time zone. Compared through the formatter rather
      // than against a literal, so the assertion does not depend on the runner's
      // locale -- what matters is that it is *this* reminder's time.
      expect(
        within(channelB).getByText(formatTimestamp("2026-08-13T12:00:00Z")),
      ).toBeTruthy();
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
