/**
 * CFG-3b's two additions to the generated form: path-mapped `errors`, and
 * `dataKeyedPaths` rendering a data-keyed object as `KeyValueTable`.
 *
 * Both are exercised against the plain component (no `AgentsSection`/
 * `SupportTemplateSection` wiring) since the behaviour under test belongs to
 * `DocumentEditor` itself -- those screens' own tests already cover that
 * default (schema-shaped) rendering is unaffected.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { DocumentEditor, type Json } from "./DocumentEditor";

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderEditor(loaded: Json, extra: Partial<Parameters<typeof DocumentEditor>[0]> = {}) {
  return render(
    <DocumentEditor
      kicker="Test document"
      subtitle="test/document.yaml"
      loaded={loaded}
      canWrite
      jsonLabel="Test document JSON"
      submitLabel="Save"
      submittingLabel="Saving..."
      readOnlyNotice="Read-only."
      notObjectMessage="Must be an object."
      onSubmit={() => Promise.resolve(null)}
      onDirtyChange={vi.fn()}
      {...extra}
    />,
    { wrapper: Wrapper },
  );
}

describe("DocumentEditor -- path-mapped errors", () => {
  it("marks the matching field invalid and shows the message as an alert under it", () => {
    renderEditor({ max_bays: 12 }, { errors: [{ path: "max_bays", message: "Must be positive" }] });
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const describedBy = input.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("Must be positive");
    expect(document.getElementById(describedBy)).toHaveAttribute("role", "alert");
  });

  it("matches a nested path through the generated object boxes", () => {
    renderEditor(
      { limits: { max_queries: 8 } },
      { errors: [{ path: "limits.max_queries", message: "Too high" }] },
    );
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("sends a path that does not resolve in the document to the page-level list, not to a field", () => {
    renderEditor(
      { max_bays: 12 },
      { errors: [{ path: "no_such_field", message: "Ghost error" }] },
    );
    const input = screen.getByRole("spinbutton");
    expect(input).not.toHaveAttribute("aria-invalid");
    const alerts = screen.getAllByRole("alert");
    expect(alerts.some((node) => node.textContent?.includes("no_such_field"))).toBe(true);
    expect(alerts.some((node) => node.textContent?.includes("Ghost error"))).toBe(true);
  });

  it("renders no error UI at all when errors is omitted", () => {
    renderEditor({ max_bays: 12 });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).not.toHaveAttribute("aria-invalid");
  });

  it("matches a bracket-index path (fields[3].priority) the same as its dotted form (RV A1)", () => {
    renderEditor(
      { fields: [{ priority: 1 }, { priority: 2 }, { priority: 3 }, { priority: 4 }] },
      { errors: [{ path: "fields[3].priority", message: "Too low" }] },
    );
    const inputs = screen.getAllByRole("spinbutton");
    expect(inputs).toHaveLength(4);
    expect(inputs[3]).toHaveAttribute("aria-invalid", "true");
    expect(inputs.slice(0, 3).every((input) => !input.hasAttribute("aria-invalid"))).toBe(true);
    // Normalised, so it also does not land on the page-level "could not
    // place" list.
    expect(screen.queryByText("fields[3].priority")).not.toBeInTheDocument();
  });
});

describe("DocumentEditor -- dataKeyedPaths", () => {
  it("renders a data-keyed object as a KeyValueTable instead of one box per key", () => {
    renderEditor(
      { ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
      { dataKeyedPaths: ["ship_via_methods"] },
    );
    // KeyValueTable's row-remove button for each entry, not ObjectNode's
    // per-key "Remove property CPU"/"Remove property XPW" -- the entries
    // themselves are data, even though `ship_via_methods` the key is schema.
    expect(screen.getByRole("button", { name: "Remove CPU" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove property CPU" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove property XPW" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("CPU");
    expect(screen.getByRole("textbox", { name: "Value for CPU" })).toHaveValue("COUNTER");
  });

  it("leaves a path not listed in dataKeyedPaths on the default per-key rendering", () => {
    renderEditor({ limits: { max_queries: 8 } });
    expect(screen.getByRole("button", { name: "Remove property max_queries" })).toBeInTheDocument();
  });

  it("shows an error under a data-keyed entry's path next to the table rather than dropping it", () => {
    renderEditor(
      { ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
      {
        dataKeyedPaths: ["ship_via_methods"],
        errors: [{ path: "ship_via_methods.XPW", message: "Unknown carrier code" }],
      },
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("ship_via_methods.XPW");
    expect(alert).toHaveTextContent("Unknown carrier code");
  });
});

describe("DocumentEditor -- rename onto an existing key inside a data-keyed table (RV B1, P5)", () => {
  it("keeps both rows, shows the duplicate alert, and blocks Save until the collision is resolved", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(null);
    renderEditor(
      { ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
      { dataKeyedPaths: ["ship_via_methods"], onSubmit },
    );

    const saveButton = screen.getByRole("button", { name: "Save" });
    // Nothing has changed yet.
    expect(saveButton).toBeDisabled();

    // Rename row 1's key ("CPU") to "XPW" -- row 2's key already.
    const key1 = screen.getByRole("textbox", { name: "Key 1" });
    await user.clear(key1);
    await user.type(key1, "XPW");

    // Both rows survive with their own values -- nothing was overwritten by
    // the collision, unlike the B1 defect (which left one row and sent
    // {"ship_via_methods":{"XPW":"PARCEL"}}, CPU's row and value gone).
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("XPW");
    expect(screen.getByRole("textbox", { name: "Key 2" })).toHaveValue("XPW");
    const values = screen.getAllByRole("textbox", { name: "Value for XPW" });
    expect(values.map((node) => (node as HTMLInputElement).value).sort()).toEqual(["COUNTER", "PARCEL"]);

    // The duplicate alert KeyValueTable already had in isolation now actually
    // fires, on both rows.
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(2);
    for (const alert of alerts) expect(alert).toHaveTextContent("This key is used more than once.");

    // Editing has visibly started (dirty), but nothing is submittable while
    // the collision stands.
    expect(saveButton).toBeDisabled();
    expect(saveButton).toHaveAttribute("title", expect.stringContaining("rename one before publishing"));

    await user.click(saveButton);
    expect(onSubmit).not.toHaveBeenCalled();

    // Resolve it by finishing the rename.
    await user.type(screen.getByRole("textbox", { name: "Key 1" }), "2");
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("XPW2");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);
    await waitFor(() => { expect(onSubmit).toHaveBeenCalledTimes(1); });
    expect(onSubmit).toHaveBeenCalledWith({ ship_via_methods: { XPW2: "COUNTER", XPW: "PARCEL" } });
  });
});
