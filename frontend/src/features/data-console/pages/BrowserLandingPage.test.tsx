import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type * as Wouter from "wouter";
import { BrowserLandingPage } from "./BrowserLandingPage";

vi.mock("wouter", async (importOriginal) => {
  const actual = await importOriginal<typeof Wouter>();
  return {
    ...actual,
    useLocation: () => ["/data-console/browser", vi.fn()],
  };
});

const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("BrowserLandingPage", () => {
  it("renders assets table", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <BrowserLandingPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Governed Data Browser")).toBeInTheDocument();
      expect(screen.getByText("SalesOrders")).toBeInTheDocument();
    });
  });
});
