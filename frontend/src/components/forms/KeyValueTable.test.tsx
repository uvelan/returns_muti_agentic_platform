import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { KeyValueTable, type KeyValueEntry } from "./KeyValueTable";

function ControlledTable({
  initial,
  valueKind = "string",
  sortable = false,
  keyPattern,
}: {
  initial: KeyValueEntry[];
  valueKind?: "string" | "number" | "boolean" | "json";
  sortable?: boolean;
  keyPattern?: RegExp;
}) {
  const [entries, setEntries] = useState(initial);
  return (
    <KeyValueTable
      label="Ship via methods"
      entries={entries}
      onChange={setEntries}
      valueKind={valueKind}
      sortable={sortable}
      keyPattern={keyPattern}
    />
  );
}

describe("KeyValueTable", () => {
  it("renders one row per entry with a named key field and a named value field", () => {
    render(
      <KeyValueTable
        label="Ship via methods"
        entries={[{ key: "CPU", value: "COUNTER" }]}
        onChange={vi.fn()}
        valueKind="string"
      />,
    );
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("CPU");
    expect(screen.getByRole("textbox", { name: "Value for CPU" })).toHaveValue("COUNTER");
  });

  it("adds a row from the new-key input, seeded with a blank value of the chosen kind", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[]} valueKind="string" />);
    await user.type(screen.getByRole("textbox", { name: "New key" }), "XPW{Enter}");
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("XPW");
    expect(screen.getByRole("textbox", { name: "Value for XPW" })).toHaveValue("");
  });

  it("renames a key in place rather than deleting and re-adding it", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "CPU", value: "COUNTER" }]} />);
    const keyInput = screen.getByRole("textbox", { name: "Key 1" });
    await user.clear(keyInput);
    await user.type(keyInput, "CPU_2");
    expect(screen.getByRole("textbox", { name: "Value for CPU_2" })).toHaveValue("COUNTER");
  });

  it("flags a duplicate key with aria-invalid and an alert, and keeps Add disabled for it", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "CPU", value: "COUNTER" }]} />);
    await user.type(screen.getByRole("textbox", { name: "New key" }), "CPU");
    expect(screen.getByRole("button", { name: "Add key" })).toBeDisabled();
  });

  it("deletes a row", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "CPU", value: "COUNTER" }]} />);
    await user.click(screen.getByRole("button", { name: "Remove CPU" }));
    expect(screen.queryByRole("textbox", { name: "Key 1" })).not.toBeInTheDocument();
  });

  it("preserves insertion order and offers no reorder controls unless sortable", () => {
    render(<ControlledTable initial={[{ key: "A", value: "1" }, { key: "B", value: "2" }]} />);
    expect(screen.queryByRole("button", { name: /Move/ })).not.toBeInTheDocument();
  });

  it("reorders rows when sortable", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "A", value: "1" }, { key: "B", value: "2" }]} sortable />);
    await user.click(screen.getByRole("button", { name: "Move A down" }));
    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(within(rows[0]).getByRole("textbox", { name: "Key 1" })).toHaveValue("B");
  });

  it("edits a boolean value with a checkbox", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "enabled", value: false }]} valueKind="boolean" />);
    const checkbox = screen.getByRole("checkbox", { name: "Value for enabled" });
    expect(checkbox).not.toBeChecked();
    await user.click(checkbox);
    expect(checkbox).toBeChecked();
  });

  it("edits a number value", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "max_queries", value: 8 }]} valueKind="number" />);
    const number = screen.getByRole("spinbutton", { name: "Value for max_queries" });
    await user.clear(number);
    await user.type(number, "12");
    expect(number).toHaveValue(12);
  });

  it("edits a json value and flags invalid JSON without losing the draft", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "limits", value: { max: 5 } }]} valueKind="json" />);
    const textarea = screen.getByRole("textbox", { name: "Value for limits" });
    await user.clear(textarea);
    await user.type(textarea, "{{not json");
    expect(textarea).toHaveAttribute("aria-invalid", "true");
    expect(textarea).toHaveValue("{not json");
  });

  it("rejects a key that does not match the required pattern", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[]} keyPattern={/^[a-z_]+$/} />);
    await user.type(screen.getByRole("textbox", { name: "New key" }), "Not Valid");
    expect(screen.getByRole("button", { name: "Add key" })).toBeDisabled();
  });
});
