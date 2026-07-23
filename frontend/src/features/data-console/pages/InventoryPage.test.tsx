import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InventoryPage } from "./InventoryPage";

const meta = {
  schema_version: "1.0",
  request_id: "inventory-request",
  generated_at: "2026-07-22T17:00:00Z",
  freshness: "LIVE",
  partial: true,
  warnings: [
    { source: "SQLSERVER", code: "TIMEOUT", message: "SQL Server inventory timed out." },
  ],
};

function installServer() {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({
    data: {
      sqlserver: null,
      mongodb: {
        database_name: "return_platform",
        observed_at: "2026-07-22T17:00:00Z",
        collections: [{
          name: "return_sessions",
          approximate_document_count: 12,
          indexes: [{ name: "_id_", is_unique: true }],
        }],
      },
      neo4j: {
        labels: ["Customer", "Return"],
        relationship_types: ["HAS_ACCOUNT"],
      },
    },
    page: null,
    meta,
  }), { status: 200, headers: { "Content-Type": "application/json" } }))));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InventoryPage />
    </QueryClientProvider>,
  );
}

describe("InventoryPage", () => {
  it("preserves healthy engines and displays partial warnings", async () => {
    installServer();
    renderPage();

    expect(await screen.findByText("return_sessions")).toBeInTheDocument();
    expect(screen.getByText("Customer")).toBeInTheDocument();
    expect(screen.getByText("HAS_ACCOUNT")).toBeInTheDocument();
    expect(screen.getByText("SQL Server inventory timed out.")).toBeInTheDocument();
    expect(screen.getByText("SQL Server inventory is unavailable in this response.")).toBeInTheDocument();
  });
});
