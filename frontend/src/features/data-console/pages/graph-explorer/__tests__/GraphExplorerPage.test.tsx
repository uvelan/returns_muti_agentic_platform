import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { GraphExplorerPage } from "../GraphExplorerPage";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("wouter", () => ({
  useRoute: vi.fn((route) => {
    if (route === "/data-console/graph/nodes/:nodeId") return [false, null];
    if (route === "/data-console/graph/relationships/:relationshipId") return [false, null];
    return [false, null];
  }),
  useLocation: () => ["/data-console/graph", vi.fn()],
  Link: ({ children, href }: { children: React.ReactNode, href: string }) => <a href={href}>{children}</a>
}));

vi.mock("../../../../../api/graphExplorerQueries", () => ({
  useGraphSearch: vi.fn(() => ({
    data: {
      data: {
        nodes: [{ id: "n1", labels: ["Test"], properties: {} }],
        relationships: []
      },
      meta: { isTruncated: false }
    },
    isLoading: false,
    isError: false
  })),
  useGraphNode: vi.fn(() => ({
    data: { id: "n1", labels: ["Test"], properties: {} },
    isLoading: false,
    isError: false
  })),
  useGraphNeighborhood: vi.fn(() => ({
    data: {
      data: {
        nodes: [],
        relationships: []
      }
    },
    isLoading: false
  }))
}));

// Mock react flow to avoid rendering errors in JSDOM
vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children: React.ReactNode }) => <div data-testid="react-flow-mock">{children}</div>,
    Background: () => <div />,
    Controls: () => <div />,
    Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>
  };
});

describe("GraphExplorerPage", () => {
  const queryClient = new QueryClient();
  
  it("renders search bar and view toggles", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <GraphExplorerPage />
      </QueryClientProvider>
    );
    
    expect(screen.getByRole("heading", { name: "Graph Explorer" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter Exact Node ID...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Table" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Canvas" })).toBeInTheDocument();
  });

  it("can toggle to Table view", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <GraphExplorerPage />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByRole("region", { name: "Graph Data Table" })).toBeInTheDocument();
  });
});
