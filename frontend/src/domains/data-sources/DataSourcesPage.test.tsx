/**
 * UI-02 -- what the data sources screen shows, and what it never shows.
 *
 * The load-bearing one is last: no credential value reaches the browser, and
 * the screen offers nowhere to type one. The rest guard the same confusions the
 * other screens do -- an empty list that is really a failed request, a control
 * offered to someone the backend will refuse -- plus the one specific to this
 * surface: an UNKNOWN probe result must not look like a healthy one.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceDetail, SourceItem } from "../../api/dataSources";
import type { SourceBinding } from "../../api/sourceBindings";
import { DataSourcesPage } from "./DataSourcesPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  listBindings: vi.fn(),
  rebind: vi.fn(),
  clear: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/dataSources", () => ({
  dataSourcesApi: { list: mocks.list, get: mocks.get },
}));

vi.mock("../../api/sourceBindings", () => ({
  CONNECTOR_TYPES: ["MONGODB", "MSSQL", "POSTGRESQL", "NEO4J"],
  sourceBindingsApi: {
    list: mocks.listBindings,
    rebind: mocks.rebind,
    clear: mocks.clear,
  },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function item(overrides: Partial<SourceItem> = {}): SourceItem {
  return {
    id: "source-mongodb",
    name: "Source MongoDB",
    engine: "MONGODB",
    environment: "LOCAL",
    ownership: "AUTHORITATIVE",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: "2026-08-11T09:00:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<SourceDetail> = {}): SourceDetail {
  return {
    ...item(),
    connectionIdentity: "mongodb/source_db",
    inventoryTotals: { assets: 2, records: null },
    lastMetadataRefresh: "2026-08-11T09:00:00Z",
    dependencyWarnings: [],
    assets: [
      {
        assetId: "source_sales",
        name: "source_db.salesInv",
        kind: "COLLECTION",
        ownership: "SOURCE",
        authoritative: true,
        writableInSandbox: false,
      },
      {
        assetId: "source_products",
        name: "source_db.products",
        kind: "COLLECTION",
        ownership: "SOURCE",
        authoritative: true,
        writableInSandbox: false,
      },
    ],
    ...overrides,
  };
}

function binding(overrides: Partial<SourceBinding> = {}): SourceBinding {
  return {
    dataset: "source_sales",
    sourceAssetId: "salesInv",
    connectorType: "MONGODB",
    connectionRef: "vault://return-platform/sources#salesInv",
    objectRef: { database: "source_db", collection: "salesInv" },
    incrementalCursorField: "updated_at",
    overridden: false,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<DataSourcesPage />, { wrapper });
}

describe("DataSourcesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.list.mockResolvedValue([item()]);
    mocks.get.mockResolvedValue(detail());
    mocks.listBindings.mockResolvedValue([binding()]);
    mocks.rebind.mockResolvedValue(undefined);
    mocks.clear.mockResolvedValue(undefined);
  });

  it("shows a loading state before the sources arrive", () => {
    mocks.list.mockReturnValue(new Promise(() => {
      // Never settles: a resolved promise races the assertion.
    }));
    mocks.listBindings.mockReturnValue(new Promise(() => {
      // Also never settles, for the same reason.
    }));
    renderPage();

    expect(screen.getAllByText("Loading...").length).toBeGreaterThan(0);
  });

  it("says no sources are configured rather than failing silently", async () => {
    mocks.list.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/No sources are configured/i)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the list could not be read rather than that there are none", async () => {
    mocks.list.mockRejectedValue(new Error("Schema and runtime resources are unavailable."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Schema and runtime resources are unavailable.",
    );
    expect(screen.queryByText(/No sources are configured/i)).toBeNull();
  });

  it("puts the source that is down at the top", async () => {
    // The reason to open this screen is usually that something is broken, and
    // a list in declaration order buries it behind three healthy rows.
    mocks.list.mockResolvedValue([
      item(),
      item({ id: "sqlserver", name: "Return Business State SQL Server", health: "UNAVAILABLE" }),
    ]);
    renderPage();

    const rows = await screen.findAllByRole("button", { name: /MONGODB|SQL_SERVER|Return Business/ });
    expect(rows[0].textContent).toContain("Return Business State SQL Server");
  });

  it("does not let an unanswered probe look healthy", async () => {
    // UNKNOWN means the probe could not answer. Rendered in the same neutral
    // tone as HEALTHY it would read as "fine", which is the one thing it is not
    // evidence of.
    mocks.list.mockResolvedValue([item({ health: "UNKNOWN" })]);
    renderPage();

    const pill = await screen.findByText("UNKNOWN");
    expect(pill.className).toContain("text-primary");
  });

  it("shows what a source exposes and why it is degraded", async () => {
    mocks.get.mockResolvedValue(
      detail({ health: "DEGRADED", dependencyWarnings: ["Replica set has no primary."] }),
    );
    renderPage();
    fireEvent.click(await screen.findByText("Source MongoDB"));

    // The tables and collections the source offers.
    expect(await screen.findByText("source_db.salesInv")).toBeTruthy();
    expect(screen.getByText("source_db.products")).toBeTruthy();
    // The probe's own safe message -- the only thing that says *why*.
    expect(screen.getByText("Replica set has no primary.")).toBeTruthy();
  });

  it("re-probes rather than only re-rendering", async () => {
    // `get_sources` fans out to the dependency probes on every call, so this is
    // a live connection test. A control that only refreshed a cache would claim
    // to have checked something it never asked about.
    renderPage();
    await screen.findByText("Source MongoDB");
    expect(mocks.list).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /re-check/i }));
    await waitFor(() => {
      expect(mocks.list).toHaveBeenCalledTimes(2);
    });
  });

  it("says a source could not be read rather than showing a blank pane", async () => {
    mocks.get.mockRejectedValue(new Error("Source not found."));
    renderPage();
    fireEvent.click(await screen.findByText("Source MongoDB"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Source not found.");
  });

  it("repoints a dataset through the binding surface", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Rebind" }));
    fireEvent.change(screen.getByLabelText("Connection for source_sales"), {
      target: { value: "vault://return-platform/sources#salesInv-restored" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rebind" }));

    await waitFor(() => {
      expect(mocks.rebind).toHaveBeenCalledTimes(1);
    });
    expect(mocks.rebind).toHaveBeenCalledWith("source_sales", {
      sourceAssetId: "salesInv",
      connectorType: "MONGODB",
      objectRef: { database: "source_db", collection: "salesInv" },
      connectionRef: "vault://return-platform/sources#salesInv-restored",
      incrementalCursorField: "updated_at",
    });
  });

  it("offers a way back to the configured binding only once one is overridden", async () => {
    renderPage();
    await screen.findByText("source_sales");
    expect(screen.queryByRole("button", { name: /follow configuration/i })).toBeNull();

    mocks.listBindings.mockResolvedValue([binding({ overridden: true })]);
    renderPage();
    const reset = await screen.findAllByRole("button", { name: /follow configuration/i });
    fireEvent.click(reset[0]);

    await waitFor(() => {
      expect(mocks.clear).toHaveBeenCalledWith("source_sales");
    });
  });

  it("does not offer to repoint a dataset to someone who may only read", async () => {
    mocks.can.mockImplementation((capability: string) => capability === "config.source.read");
    renderPage();

    await screen.findByText("source_sales");
    expect(screen.getByRole("button", { name: "Rebind" })).toBeDisabled();
  });

  /**
   * The defect this pair exists to keep fixed.
   *
   * The rebind routes require `config.source.rebind`, which only admins hold.
   * The screen gated on `config.source.write`, which a `WORKSPACE_EDITOR` holds
   * -- so an editor was offered a Rebind button whose `PUT` the backend refuses
   * with a 403. The first case is the one that used to pass wrongly; the second
   * proves the gate is a gate and not a hardcoded `false`.
   */
  it("does not offer Rebind on config.source.write alone", async () => {
    mocks.can.mockImplementation((capability: string) =>
      capability === "config.source.read" || capability === "config.source.write",
    );
    renderPage();

    await screen.findByText("source_sales");
    expect(screen.getByRole("button", { name: "Rebind" })).toBeDisabled();
    // Named, so the reader knows what to ask for rather than seeing a dead control.
    expect(screen.getByText(/config\.source\.rebind/)).toBeTruthy();
  });

  it("offers Rebind on config.source.rebind", async () => {
    mocks.can.mockImplementation((capability: string) =>
      capability === "config.source.read" || capability === "config.source.rebind",
    );
    renderPage();

    await screen.findByText("source_sales");
    expect(screen.getByRole("button", { name: "Rebind" })).toBeEnabled();
    expect(screen.queryByText(/config\.source\.rebind/)).toBeNull();
  });

  it("shows nothing at all without the source read", () => {
    mocks.can.mockReturnValue(false);
    renderPage();

    expect(screen.getByText(/requires config.source.read/i)).toBeTruthy();
    expect(mocks.list).not.toHaveBeenCalled();
  });

  it("surfaces a refused rebinding instead of reporting it was applied", async () => {
    mocks.rebind.mockRejectedValue(new Error("object_ref must contain non-empty keys and values"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Rebind" }));
    fireEvent.change(screen.getByLabelText("Connection for source_sales"), {
      target: { value: "vault://x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rebind" }));

    expect(await screen.findByText(/object_ref must contain non-empty keys/)).toBeTruthy();
  });

  /**
   * The security assertion.
   *
   * Two halves, because a leak has two shapes. The screen must not *render* a
   * credential even when one is somehow present in a payload, and it must not
   * *collect* one -- there is no field on `RebindRequest` for a password, DSN
   * or connection string, so a box for one would be a box posting a value the
   * backend forbids into a request it would reject, after the browser had
   * already held it.
   */
  describe("credentials", () => {
    it("renders no credential value, even when a payload carries one", async () => {
      // Deliberately hostile fixtures. `SourceDetail` and `SourceBindingView`
      // both declare `extra="forbid"` and have no such field, and `/api/config`
      // scrubs every response through `redact_secret_values` -- so these values
      // cannot arrive. The screen must not display them if that ever changes.
      const secret = "S3cr3t-Passw0rd-Value";
      mocks.get.mockResolvedValue({
        ...detail(),
        password: secret,
        connectionString: `mongodb://admin:${secret}@mongodb:27017`,
      });
      mocks.listBindings.mockResolvedValue([
        { ...binding(), password: secret, dsn: `mongodb://admin:${secret}@mongodb:27017` },
      ]);
      renderPage();
      fireEvent.click(await screen.findByText("Source MongoDB"));
      await screen.findByText("source_db.salesInv");

      expect(document.body.textContent).not.toContain(secret);
      expect(document.body.textContent).not.toContain("mongodb://admin:");
      // The reference is not a secret and must survive: an operator has to be
      // able to see *which* secret a binding points at.
      expect(screen.getByText("vault://return-platform/sources#salesInv")).toBeTruthy();
    });

    it("offers no field a credential could be typed into", async () => {
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "Rebind" }));

      const fields = [
        ...screen.queryAllByRole("textbox"),
        ...Array.from(document.querySelectorAll("input")),
      ];
      for (const field of fields) {
        expect(field.getAttribute("type")).not.toBe("password");
        const label = `${field.getAttribute("aria-label") ?? ""} ${field.getAttribute("name") ?? ""}`;
        expect(label.toLowerCase()).not.toMatch(
          /password|secret|token|credential|api[_-]?key|dsn|connection[_-]?string/,
        );
      }
      // The one field there is takes a pointer, and the screen says so.
      expect(screen.getByLabelText("Connection for source_sales")).toBeTruthy();
      expect(
        screen.getByText(/no credential is ever entered here or returned to this browser/i),
      ).toBeTruthy();
    });
  });
});
