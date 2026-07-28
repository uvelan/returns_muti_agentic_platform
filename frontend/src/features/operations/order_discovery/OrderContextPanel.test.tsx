import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FIXTURE_ASSOCIATE_CONVERSATIONS } from "../../../fixtures/associateReturns";
import { OrderContextPanel, parseApiUtcTimestamp } from "./OrderContextPanel";

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
});
