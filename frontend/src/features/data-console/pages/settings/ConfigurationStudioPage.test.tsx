import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigurationStudioPage } from "./ConfigurationStudioPage";

const mockSaveDomain = vi.fn();
const mockPromoteRelease = vi.fn();
const mockCreateRelease = vi.fn();

vi.mock("../../../../api/configurationQueries", () => ({
  useActiveSnapshot: () => ({
    data: {
      release_id: "rel-active-1",
      checksum_sha256: "abc1234567890def123",
      loaded_at: "2026-07-27T10:00:00Z",
      source: "NEO4J_CONFIGURATION_GRAPH",
      configuration: {},
      domain_payloads: { RETURN_PLATFORM: { max_candidates: 5 } },
    },
    isLoading: false,
    isError: false,
  }),
  useConfigurationReleases: () => ({
    data: [
      {
        release_id: "rel-active-1",
        status: "PINNED",
        created_at: "2026-07-27T10:00:00Z",
        created_by: "system",
        checksum_sha256: "abc123",
      },
      {
        release_id: "rel-draft-2",
        status: "DRAFT",
        created_at: "2026-07-27T11:00:00Z",
        created_by: "admin-1",
        checksum_sha256: "def456",
      },
    ],
    isLoading: false,
  }),
  useConfigurationReleaseDetail: (id: string | null) => ({
    data: id === "rel-draft-2" ? {
      release_id: "rel-draft-2",
      status: "DRAFT",
      created_at: "2026-07-27T11:00:00Z",
      created_by: "admin-1",
      checksum_sha256: "def456",
      domains: {
        RETURN_PLATFORM: { max_candidates: 10, lucence_fuzzy_distance: 2 },
        AI_GATEWAY: { tasks: { RETURN_ELIGIBILITY_V1: { promptVersion: "v2" } } },
        DEPENDENCY_SIMULATION: { enabled: true },
      },
    } : {
      release_id: "rel-active-1",
      status: "PINNED",
      created_at: "2026-07-27T10:00:00Z",
      created_by: "system",
      checksum_sha256: "abc123",
      domains: {
        RETURN_PLATFORM: { max_candidates: 5 },
      },
    },
    isLoading: false,
  }),
  useCreateReleaseMutation: () => ({
    mutate: mockCreateRelease,
    isPending: false,
  }),
  useSaveDomainMutation: () => ({
    mutate: mockSaveDomain,
    isPending: false,
  }),
  usePromoteReleaseMutation: () => ({
    mutate: mockPromoteRelease,
    isPending: false,
  }),
}));

const createTestQueryClient = () => new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe("ConfigurationStudioPage", () => {
  it("renders active runtime snapshot and release list", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ConfigurationStudioPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Graph Configuration Studio")).toBeInTheDocument();
      expect(screen.getByText("Neo4j Graph Active")).toBeInTheDocument();
      expect(screen.getAllByText("rel-active-1").length).toBeGreaterThan(0);
      expect(screen.getByText("rel-draft-2")).toBeInTheDocument();
    });
  });

  it("allows selecting a draft release and saving domain configuration", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ConfigurationStudioPage />
      </QueryClientProvider>
    );

    // Click on draft release
    const draftItem = await screen.findByText("rel-draft-2");
    fireEvent.click(draftItem);

    await waitFor(() => {
      expect(screen.getByText("Save Domain Config")).toBeInTheDocument();
    });

    // Click Save
    const saveBtn = screen.getByText("Save Domain Config");
    fireEvent.click(saveBtn);

    expect(mockSaveDomain).toHaveBeenCalledWith(
      expect.objectContaining({
        releaseId: "rel-draft-2",
        domainKey: "RETURN_PLATFORM",
      }),
      expect.any(Object)
    );
  });

  it("allows editing every graph-backed behavior domain", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ConfigurationStudioPage />
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByText("rel-draft-2"));
    fireEvent.click(await screen.findByRole("button", { name: "AI_GATEWAY" }));
    fireEvent.click(screen.getByText("Save Domain Config"));

    expect(mockSaveDomain).toHaveBeenCalledWith(
      expect.objectContaining({
        releaseId: "rel-draft-2",
        domainKey: "AI_GATEWAY",
      }),
      expect.any(Object)
    );
  });
});
