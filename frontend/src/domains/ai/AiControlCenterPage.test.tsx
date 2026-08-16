/**
 * The Interceptions tab, and the manual-response flow D2 unblocked.
 *
 * Two things here are worth more than the happy path:
 *
 * **The queue rendered blanks and nothing failed.** `InterceptionRow` was
 * transcribed in snake_case while the backend emits camelCase, and every field
 * was optional, so a total mismatch looked exactly like an empty queue. The
 * first test below fails against the old shape.
 *
 * **The prompt is fetched only when a responder opens.** It is sealed at rest
 * because it can carry rows read out of a customer's database; listing the
 * queue must not decrypt it. Asserted by counting calls, not by inspecting
 * markup, because "did not render it" and "did not fetch it" are different
 * claims and only the second is the one that matters.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AiControlCenterPage } from "./AiControlCenterPage";

const mocks = vi.hoisted(() => ({
  listInterceptions: vi.fn(),
  readInterceptionRequest: vi.fn(),
  answerInterception: vi.fn(),
  cancelInterception: vi.fn(),
  allowInterception: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/aiControlCenter", () => ({
  aiControlCenterApi: {
    listRoutes: vi.fn().mockResolvedValue([]),
    listTasks: vi.fn().mockResolvedValue([]),
    listAttempts: vi.fn().mockResolvedValue([]),
    getSummary: vi.fn().mockResolvedValue({}),
    listInterceptions: mocks.listInterceptions,
    readInterceptionRequest: mocks.readInterceptionRequest,
    answerInterception: mocks.answerInterception,
    cancelInterception: mocks.cancelInterception,
    allowInterception: mocks.allowInterception,
  },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

const PENDING = {
  interceptionId: "int-1",
  taskId: "GRAPH_SCHEMA_PROPOSAL_V1",
  status: "PENDING",
  point: "REQUEST" as const,
  createdAt: "2026-08-10T10:00:00Z",
  expiresAt: "2026-08-10T11:00:00Z",
  answeredBy: null,
};

/** The second interception point: a reply that came back and is being reviewed. */
const HELD_RESPONSE = {
  interceptionId: "resp-1",
  taskId: "ORDER_AGENT_REASONING_V1",
  status: "PENDING",
  point: "RESPONSE" as const,
  createdAt: "2026-08-10T10:00:00Z",
  expiresAt: "2026-08-10T11:00:00Z",
  answeredBy: null,
};

const SEALED_RESPONSE_PAYLOAD = {
  systemPrompt: "decide",
  payload: { contextJson: "{}" },
  modelResponse: {
    provider: "GOOGLE",
    model: "gemini-flash",
    text: '{"action":"SEARCH"}',
    digest: "abc",
  },
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}


/**
 * Put the screen on a section.
 *
 * Sections moved from local state into the URL when the sidebar took over
 * navigation, so a test selects one by being at its path rather than by
 * clicking a tab that no longer exists. `wouter` reads `window.location`, so
 * pushing history before render is all that is required -- and it exercises
 * the same deep-link path a bookmark would.
 */
function goToSection(domainPath: string, slug: string): void {
  window.history.pushState(null, "", `${domainPath}/${slug}`);
}

async function openInterceptionsTab() {
  goToSection("/ai", "interceptions");
  render(<AiControlCenterPage />, { wrapper });
  await waitFor(() => {
    expect(mocks.listInterceptions).toHaveBeenCalled();
  });
}

describe("AI Control Center interceptions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listInterceptions.mockResolvedValue([PENDING]);
    mocks.readInterceptionRequest.mockResolvedValue({ prompt: "sealed content" });
    mocks.answerInterception.mockResolvedValue({
      interceptionId: "int-1",
      status: "ANSWERED",
      answeredBy: "operator",
      answeredAt: "2026-08-10T10:05:00Z",
    });
    mocks.cancelInterception.mockResolvedValue({
      interceptionId: "int-1",
      status: "CANCELLED",
      answeredBy: null,
      answeredAt: null,
    });
    mocks.allowInterception.mockResolvedValue({
      interceptionId: "int-1",
      status: "ALLOWED",
      answeredBy: "operator",
      answeredAt: "2026-08-10T10:05:00Z",
    });
  });

  it("renders the backend's camelCase fields", async () => {
    await openInterceptionsTab();

    // Against the old snake_case shape every one of these was "-".
    expect(await screen.findByText("int-1")).toBeInTheDocument();
    expect(screen.getByText("GRAPH_SCHEMA_PROPOSAL_V1")).toBeInTheDocument();
  });

  it("does not fetch the sealed prompt while merely listing the queue", async () => {
    await openInterceptionsTab();
    await screen.findByText("int-1");

    expect(mocks.readInterceptionRequest).not.toHaveBeenCalled();
  });

  it("unseals the prompt only when a responder is opened", async () => {
    await openInterceptionsTab();
    fireEvent.click(await screen.findByRole("button", { name: "Respond manually" }));

    await waitFor(() => {
      expect(mocks.readInterceptionRequest).toHaveBeenCalledWith("int-1");
    });
    expect(await screen.findByText(/sealed content/)).toBeInTheDocument();
  });

  it("submits the operator's text", async () => {
    await openInterceptionsTab();
    fireEvent.click(await screen.findByRole("button", { name: "Respond manually" }));
    await screen.findByText(/sealed content/);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "use candidate two" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    await waitFor(() => {
      expect(mocks.answerInterception).toHaveBeenCalledWith("int-1", "use candidate two");
    });
  });

  it("refuses to submit before the prompt has loaded", async () => {
    // Answering a prompt you have not seen is the failure a manual path exists
    // to prevent, and the backend cannot tell the difference.
    let release: (value: unknown) => void = () => undefined;
    mocks.readInterceptionRequest.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    await openInterceptionsTab();
    fireEvent.click(await screen.findByRole("button", { name: "Respond manually" }));

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "premature" } });

    expect(screen.getByRole("button", { name: "Submit answer" })).toBeDisabled();
    release({ prompt: "arrived" });
  });

  it("surfaces a conflict rather than retrying it", async () => {
    // Two operators answering one prompt is a real situation. The second needs
    // to know their text was not recorded.
    mocks.answerInterception.mockRejectedValue(new Error("interception 'int-1' is ANSWERED"));
    await openInterceptionsTab();
    fireEvent.click(await screen.findByRole("button", { name: "Respond manually" }));
    await screen.findByText(/sealed content/);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "too late" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(await screen.findByText(/is ANSWERED/)).toBeInTheDocument();
    expect(mocks.answerInterception).toHaveBeenCalledTimes(1);
  });

  it("cancels a held request without answering it", async () => {
    // Without this the only ways out of PENDING are to answer or to wait for
    // the expiry, so an operator who can see the prompt is wrong has to invent
    // an answer or leave a caller blocked.
    await openInterceptionsTab();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(mocks.cancelInterception).toHaveBeenCalledWith("int-1");
    });
    // Cancelling must not unseal the prompt: it needs no reading.
    expect(mocks.readInterceptionRequest).not.toHaveBeenCalled();
  });

  it("surfaces a cancel that lost the race", async () => {
    // `store.cancel` is silent when the record is not PENDING, so the backend
    // reads the outcome back and 409s. That has to reach the operator: their
    // cancel did nothing because an answer landed first.
    mocks.cancelInterception.mockRejectedValue(
      new Error("interception 'int-1' is ANSWERED and was not cancelled"),
    );
    await openInterceptionsTab();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("was not cancelled");
  });

  it("counts the statuses the backend actually emits", async () => {
    // These tiles counted CLAIMED and RESPONDED, which `InterceptionStatus`
    // does not have -- both read zero everywhere, which looks like a quiet
    // queue rather than a wrong label.
    await openInterceptionsTab();
    // The tiles render with the table, so wait for the row rather than for the
    // fetch merely having been issued.
    await screen.findByText("int-1");

    expect(screen.getByText("Answered")).toBeInTheDocument();
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    expect(screen.queryByText("Claimed")).toBeNull();
    expect(screen.queryByText("Responded")).toBeNull();
  });

  it("shows both hold points in one queue, distinguishable at a glance", async () => {
    // Two queues would be two screens to remember to open. One queue is only
    // acceptable if a row says which job it is without being clicked.
    mocks.listInterceptions.mockResolvedValue([PENDING, HELD_RESPONSE]);
    await openInterceptionsTab();
    await screen.findByText("int-1");

    expect(screen.getByText("resp-1")).toBeInTheDocument();
    expect(screen.getByText("Request")).toBeInTheDocument();
    expect(screen.getByText("Response")).toBeInTheDocument();
    expect(screen.getByText("Held requests")).toBeInTheDocument();
    expect(screen.getByText("Held responses")).toBeInTheDocument();
    // And the actions are named for the job, not for the endpoint.
    expect(screen.getByRole("button", { name: "Respond manually" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review response" })).toBeInTheDocument();
  });

  it("pre-fills the model's reply when reviewing a response", async () => {
    // The whole ergonomic difference between the points. At REQUEST you write
    // from nothing; at RESPONSE you edit what came back. An editor that made
    // you retype it would turn every edit into a rewrite.
    mocks.listInterceptions.mockResolvedValue([HELD_RESPONSE]);
    mocks.readInterceptionRequest.mockResolvedValue(SEALED_RESPONSE_PAYLOAD);
    await openInterceptionsTab();

    fireEvent.click(await screen.findByRole("button", { name: "Review response" }));

    await waitFor(() => {
      expect(screen.getByRole("textbox")).toHaveValue('{"action":"SEARCH"}');
    });
    // And it says what the edit will be recorded as -- neither the model nor a
    // plain manual answer.
    expect(screen.getByText(/HUMAN_EDITED/)).toBeInTheDocument();
    expect(screen.getByText(/GOOGLE\/gemini-flash/)).toBeInTheDocument();
  });

  it("submits an edited response through the same answer route", async () => {
    mocks.listInterceptions.mockResolvedValue([HELD_RESPONSE]);
    mocks.readInterceptionRequest.mockResolvedValue(SEALED_RESPONSE_PAYLOAD);
    await openInterceptionsTab();
    fireEvent.click(await screen.findByRole("button", { name: "Review response" }));
    await waitFor(() => {
      expect(screen.getByRole("textbox")).toHaveValue('{"action":"SEARCH"}');
    });

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '{"action":"CONFIRM_ORDER"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit edit" }));

    await waitFor(() => {
      expect(mocks.answerInterception).toHaveBeenCalledWith(
        "resp-1",
        '{"action":"CONFIRM_ORDER"}',
      );
    });
  });

  it("accepts a held response unchanged without unsealing it into an edit", async () => {
    // Accept and edit must be different acts. Routing "unchanged" through the
    // answer route would record a human answer whose text happens to match the
    // model's, and the row would then read as human-authored forever.
    mocks.listInterceptions.mockResolvedValue([HELD_RESPONSE]);
    await openInterceptionsTab();

    fireEvent.click(await screen.findByRole("button", { name: "Accept unchanged" }));

    await waitFor(() => {
      expect(mocks.allowInterception).toHaveBeenCalledWith("resp-1");
    });
    expect(mocks.answerInterception).not.toHaveBeenCalled();
  });

  it("rejects a held response", async () => {
    mocks.listInterceptions.mockResolvedValue([HELD_RESPONSE]);
    await openInterceptionsTab();

    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => {
      expect(mocks.cancelInterception).toHaveBeenCalledWith("resp-1");
    });
  });

  it("reads the same status differently at each point", async () => {
    // `ANSWERED` on a request is a substitution; on a response it is an edit.
    // One state machine, two readings, and the queue is where somebody has to
    // be able to tell them apart.
    mocks.listInterceptions.mockResolvedValue([
      { ...PENDING, status: "ANSWERED", answeredBy: "operator" },
      { ...HELD_RESPONSE, status: "ANSWERED", answeredBy: "operator" },
    ]);
    await openInterceptionsTab();
    await screen.findByText("int-1");

    expect(screen.getByText("(Answered by human)")).toBeInTheDocument();
    expect(screen.getByText("(Edited by human)")).toBeInTheDocument();
  });

  it("offers no responder without the act capability", async () => {
    // `.read` lists the queue; `.act` unseals and answers. A button that 403s is
    // worse than no button.
    mocks.can.mockImplementation((capability: string) => capability !== "ai.interception.act");
    await openInterceptionsTab();
    await screen.findByText("int-1");

    expect(screen.queryByRole("button", { name: "Respond manually" })).toBeNull();
  });
});
