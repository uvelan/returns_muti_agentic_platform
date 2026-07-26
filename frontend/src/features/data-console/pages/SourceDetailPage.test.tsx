import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type * as Wouter from "wouter";
import { SourceDetailPage } from "./SourceDetailPage";

// Mock wouter
vi.mock("wouter", async (importOriginal) => {
  const actual = await importOriginal<typeof Wouter>();
  return {
    ...actual,
    useLocation: () => ["/data-console/sources/src-sql-omc", vi.fn()],
    useParams: () => ({ sourceId: "src-sql-omc" }),
  };
});

const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("SourceDetailPage", () => {
  it("renders source details and tabs", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <SourceDetailPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("OMC SQL Server")).toBeInTheDocument();
      expect(screen.getByText("Source ID: src-sql-omc")).toBeInTheDocument();
      expect(screen.getByText("Configuration Summary")).toBeInTheDocument();
    });
  });
});
