import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfigurationStudioV2Page } from "./ConfigurationStudioV2Page";

type SaveVariables = { releaseId: string; domainKey: string; payload: Record<string, unknown> };
const mockSaveDomain = vi.fn<(variables: SaveVariables, options: { onSuccess?: () => void }) => void>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

vi.mock("../../../../api/configurationQueries", () => ({
  useActiveSnapshot: () => ({ data: { release_id: "rel-active-1", checksum_sha256: "abc123", loaded_at: "2026-08-02T10:00:00Z", source: "NEO4J_CONFIGURATION_GRAPH", configuration: {}, domain_payloads: {} }, isLoading: false, isError: false }),
  useConfigurationReleases: () => ({ data: [
    { release_id: "rel-active-1", status: "PINNED", created_at: "2026-08-02T10:00:00Z", created_by: "system", checksum_sha256: "abc" },
    { release_id: "rel-draft-2", status: "DRAFT", created_at: "2026-08-02T11:00:00Z", created_by: "admin", checksum_sha256: "def" },
  ], isLoading: false }),
  useConfigurationReleaseDetail: (id: string | null) => ({ data: {
    release_id: id ?? "rel-active-1", status: id === "rel-draft-2" ? "DRAFT" : "PINNED", created_at: "2026-08-02T10:00:00Z", created_by: "system", checksum_sha256: "abc",
    domains: { RETURN_PLATFORM: { agents: {
      order_discovery: { name: "Order Discovery Agent", enabled: true, ai_assisted: true, capabilities: ["normalize_evidence", "rank_candidates"] },
      return_workflow: { name: "Return Workflow Agent", enabled: true },
    }, discovery: { max_candidates: 5, thresholds: { ambiguity_gap: 0.1 } } }, AI_GATEWAY: { enabled: true } },
  }, isLoading: false }),
  useSaveDomainMutation: () => ({ mutate: mockSaveDomain, isPending: false }),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ConfigurationStudioV2Page /></QueryClientProvider>);
}

describe("ConfigurationStudioV2Page", () => {
  it("shows agent-owned modules with typed controls instead of a JSON editor", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("rel-draft-2"));
    expect((await screen.findAllByText("Order Discovery Agent")).length).toBeGreaterThan(0);
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeInTheDocument();
    expect(screen.queryByText(/Domain Payload JSON/i)).not.toBeInTheDocument();
  });

  it("edits nested values and saves the compatible domain payload", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("rel-draft-2"));
    const discoveryLabel = await screen.findByText("Discovery");
    const discoveryButton = discoveryLabel.closest("button");
    expect(discoveryButton).not.toBeNull();
    if (discoveryButton) fireEvent.click(discoveryButton);
    fireEvent.change(await screen.findByLabelText("Max Candidates"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Save module" }));
    await waitFor(() => { expect(mockSaveDomain).toHaveBeenCalledTimes(1); });
    const variables = mockSaveDomain.mock.calls[0][0];
    const discovery = variables.payload.discovery;
    expect(variables.releaseId).toBe("rel-draft-2");
    expect(variables.domainKey).toBe("RETURN_PLATFORM");
    expect(isRecord(discovery) ? discovery.max_candidates : undefined).toBe(8);
  });
});

