import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CopilotV2Page } from "./CopilotV2Page";

vi.mock("../../api/associateReturns", () => ({
  COPILOT_V2_BASE: "/api/v2/copilot",
  confirmAssociateDiscovery: vi.fn(),
  continueAssociateChat: vi.fn(),
  listAssociateConversations: vi.fn().mockResolvedValue([]),
  startAssociateChat: vi.fn(),
  submitAssociateReturnDetails: vi.fn(),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <CopilotV2Page />
    </QueryClientProvider>,
  );
}

describe("CopilotV2Page", () => {
  it("renders the responsive order-discovery welcome workspace", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Returns Assistant" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "How can I help you today?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Return for order SO-00010001/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Ask Copilot about returns")).toBeInTheDocument();
  });

  it("opens and closes the responsive context drawer", () => {
    renderPage();

    fireEvent.click(screen.getAllByRole("button", { name: "Open context" })[0]);
    expect(screen.getByText("Order context")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close context" }));
    expect(screen.queryByText("Order context")).not.toBeInTheDocument();
  });
});
