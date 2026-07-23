import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type * as Wouter from "wouter";
import { RecordDetailPage } from "./RecordDetailPage";

vi.mock("wouter", async (importOriginal) => {
  const actual = await importOriginal<typeof Wouter>();
  return {
    ...actual,
    useLocation: () => ["/data-console/browser/SQL_SERVER/sales-orders/records/SO-1001", vi.fn()],
    useParams: () => ({ engine: "SQL_SERVER", assetId: "sales-orders", recordId: "SO-1001" }),
  };
});

const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("RecordDetailPage", () => {
  it("renders record data and properties", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RecordDetailPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Record: SO-1001")).toBeInTheDocument();
      expect(screen.getByText("REDACTED")).toBeInTheDocument(); // Secret hash
    });
  });
});

