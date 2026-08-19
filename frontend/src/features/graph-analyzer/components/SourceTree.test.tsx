import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AnalyzerSource } from "../../../contracts/graphAnalyzer";
import { SourceTree } from "./SourceTree";

const sources: readonly AnalyzerSource[] = [{
  id: "source-1",
  name: "Orders source",
  engine: "POSTGRESQL",
  status: "CONNECTED",
  port: 5432,
  host: "db.internal",
  database: "orders",
  username: null,
  lastValidatedAt: null,
  objectCount: 1,
  objects: [{ id: "db-1", name: "orders", kind: "database", path: ["orders"], selectable: true, children: [{ id: "table-1", name: "customer_orders", kind: "table", path: ["orders", "public", "customer_orders"], selectable: true, children: [] }] }],
}];

describe("SourceTree", () => {
  it("labels sources read-only and selects descendants explicitly", async () => {
    const onSelectionChange = vi.fn();
    const user = userEvent.setup();
    render(<SourceTree sources={sources} selectedIds={new Set()} activeId={null} onSelectionChange={onSelectionChange} onActivate={vi.fn()} />);
    expect(screen.getByText("READ ONLY")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Select orders" }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set(["db-1", "table-1"]));
  });
});
