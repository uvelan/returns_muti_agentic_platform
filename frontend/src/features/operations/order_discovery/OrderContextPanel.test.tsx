import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FIXTURE_ASSOCIATE_CONVERSATIONS } from "../../../fixtures/associateReturns";
import { OrderContextPanel } from "./OrderContextPanel";
import { parseApiUtcTimestamp } from "./timestamps";

describe("OrderContextPanel progressive clarification", () => {
  it("treats timezone-less API timestamps as UTC", () => {
    expect(parseApiUtcTimestamp("2026-07-28T10:53:16.996000")).toBe(
      Date.parse("2026-07-28T10:53:16.996000Z"),
    );
  });

  it("shows only the selected field values while candidates remain ambiguous", () => {
    const onSelectClarification = vi.fn();
    const conversation = {
      ...FIXTURE_ASSOCIATE_CONVERSATIONS[0],
      status: "DISCOVERY_CLARIFICATION_REQUIRED",
      activeRequestedSlots: ["customer_name"],
      clarificationPrompt: {
        slot: "customer_name",
        question: "Which customer are you referring to?",
        options: [
          { value: "Maya Foster", label: "Maya Foster", candidateCount: 2 },
          { value: "Nadia Diaz", label: "Nadia Diaz", candidateCount: 1 },
        ],
      },
    };

    render(
      <OrderContextPanel
        conversation={conversation}
        candidateIndex={0}
        selectedLineId=""
        onSelectCandidate={vi.fn()}
        onSelectLine={vi.fn()}
        onSelectClarification={onSelectClarification}
        onConfirmDiscovery={vi.fn()}
        isConfirming={false}
        isClarifying={false}
      />,
    );

    expect(screen.getByText("Which customer are you referring to?")).toBeInTheDocument();
    expect(screen.queryByText("ORD-10001")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Maya Foster/ }));
    expect(onSelectClarification).toHaveBeenCalledWith("Maya Foster");
  });

  it("never exposes order cards or confirmation for generic ambiguity", () => {
    const firstCandidate = FIXTURE_ASSOCIATE_CONVERSATIONS[0].candidates[0];
    const conversation = {
      ...FIXTURE_ASSOCIATE_CONVERSATIONS[0],
      status: "DISCOVERY_CLARIFICATION_REQUIRED",
      nextQuestion: "What is the customer's full name?",
      activeRequestedSlots: [],
      clarificationPrompt: null,
      candidates: [],
    };

    render(
      <OrderContextPanel
        conversation={conversation}
        candidateIndex={0}
        selectedLineId={firstCandidate.lines[0]?.orderLineId ?? ""}
        onSelectCandidate={vi.fn()}
        onSelectLine={vi.fn()}
        onSelectClarification={vi.fn()}
        onConfirmDiscovery={vi.fn()}
        isConfirming={false}
        isClarifying={false}
      />,
    );

    expect(screen.getByText("What is the customer's full name?")).toBeInTheDocument();
    expect(screen.getByText(/remain hidden until/i)).toBeInTheDocument();
    expect(screen.queryByText("ORD-10001")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Confirm selected item/i }),
    ).not.toBeInTheDocument();
  });
});
