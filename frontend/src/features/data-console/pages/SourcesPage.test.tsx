import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type * as Wouter from "wouter";
import { SourcesPage } from "./SourcesPage";

// Mock wouter
vi.mock("wouter", async (importOriginal) => {
  const actual = await importOriginal<typeof Wouter>();
  return {
    ...actual,
    useLocation: () => ["/data-console/sources", vi.fn()],
  };
});

// Provide query client
const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("SourcesPage", () => {
  it("renders sources and fixture notice", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <SourcesPage />
      </QueryClientProvider>
    );

    // Wait for fixture notice
    await waitFor(() => {
      expect(screen.getByText(/FIXTURE — NON-DURABLE/)).toBeInTheDocument();
    });

    // Wait for sources to load
    await waitFor(() => {
      expect(screen.getByText("OMC SQL Server")).toBeInTheDocument();
      expect(screen.getByText("Returns MongoDB")).toBeInTheDocument();
    });
  });
});
