import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type * as Wouter from "wouter";
import { AssetBrowserPage } from "./AssetBrowserPage";

vi.mock("wouter", async (importOriginal) => {
  const actual = await importOriginal<typeof Wouter>();
  return {
    ...actual,
    useLocation: () => ["/data-console/browser/SQL_SERVER/sales-orders", vi.fn()],
    useParams: () => ({ engine: "SQL_SERVER", assetId: "sales-orders" }),
  };
});

const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("AssetBrowserPage", () => {
  it("renders records and capabilities", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AssetBrowserPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("SalesOrders")).toBeInTheDocument();
      expect(screen.getByText("SO-1001")).toBeInTheDocument();
      expect(screen.getByText(/Read-only governed inspection/i)).toBeInTheDocument();
    });
  });
});

