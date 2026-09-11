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

  // A5 (CFG-3b RV, carried to CFG-4): the new-key input had `aria-invalid`
  // and a disabled Add button but no element either pointed to -- `title` on
  // a disabled button is not reliably announced. It now has a real,
  // `aria-describedby`-wired error, the same convention every row's own key
  // error already uses.
  it("gives the new-key input a real, described error for a taken key", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "CPU", value: "COUNTER" }]} />);
    const newKey = screen.getByRole("textbox", { name: "New key" });
    await user.type(newKey, "CPU");
    expect(newKey).toHaveAttribute("aria-invalid", "true");
    const describedBy = newKey.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent("That key already exists.");
  });

  it("gives the new-key input a real, described error for a key that fails the pattern", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[]} keyPattern={/^[a-z_]+$/} />);
    const newKey = screen.getByRole("textbox", { name: "New key" });
    await user.type(newKey, "Not Valid");
    const describedBy = newKey.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "Does not match the required pattern.",
    );
  });

  it("has no new-key error element while the draft is empty or valid", () => {
    render(<ControlledTable initial={[]} />);
    expect(screen.getByRole("textbox", { name: "New key" })).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // A6 (CFG-3b RV, carried to CFG-4): a table-level error, for a refusal
  // that names the whole mapping rather than one row -- an empty required map.
  it("shows a table-level error as an alert", () => {
    render(
      <KeyValueTable
        label="Ship via methods"
        error="At least one entry is required."
        entries={[]}
        onChange={vi.fn()}
        valueKind="string"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("At least one entry is required.");
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

  it("keeps each remaining row's own value and JSON draft after deleting the middle row", async () => {
    const user = userEvent.setup();
    render(
      <ControlledTable
        initial={[
          { key: "a", value: { n: 1 } },
          { key: "b", value: { n: 2 } },
          { key: "c", value: { n: 99 } },
        ]}
        valueKind="json"
      />,
    );

    // Rows "a" and "c" (the ones that survive) get their own in-progress,
    // still-invalid drafts -- distinct from each other and from their
    // committed values -- so a mix-up after the delete would be visible.
    const aTextarea = screen.getByRole("textbox", { name: "Value for a" });
    await user.clear(aTextarea);
    await user.type(aTextarea, '{{"n": 1, "still typing a');
    const cTextarea = screen.getByRole("textbox", { name: "Value for c" });
    await user.clear(cTextarea);
    await user.type(cTextarea, '{{"n": 99, "still typing c');
    expect(aTextarea).toHaveAttribute("aria-invalid", "true");
    expect(cTextarea).toHaveAttribute("aria-invalid", "true");

    await user.click(screen.getByRole("button", { name: "Remove b" }));

    // Reindexed, not reset: row "a" is still row 1 with its own draft, row
    // "c" is now row 2, still with its own (different) draft -- neither
    // dropped, neither handed to the other row, neither snapped back to its
    // last committed value.
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("a");
    expect(screen.getByRole("textbox", { name: "Key 2" })).toHaveValue("c");
    expect(screen.getByRole("textbox", { name: "Value for a" })).toHaveValue('{"n": 1, "still typing a');
    expect(screen.getByRole("textbox", { name: "Value for c" })).toHaveValue('{"n": 99, "still typing c');
  });

  it("flags both rows in place when a rename collides with another row's key, without dropping either", async () => {
    const user = userEvent.setup();
    render(<ControlledTable initial={[{ key: "A", value: "1" }, { key: "B", value: "2" }]} />);
    const key1 = screen.getByRole("textbox", { name: "Key 1" });
    await user.clear(key1);
    await user.type(key1, "B");

    // Both rows survive, both still show their own value, and both are
    // flagged -- a duplicate key does not silently collapse two rows into
    // one the way it did before ObjectNode kept `entries` as its own state.
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveValue("B");
    expect(screen.getByRole("textbox", { name: "Key 2" })).toHaveValue("B");
    const values = screen.getAllByRole("textbox", { name: "Value for B" });
    expect(values.map((node) => (node as HTMLInputElement).value).sort()).toEqual(["1", "2"]);

    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(2);
    for (const alert of alerts) expect(alert).toHaveTextContent("This key is used more than once.");
    expect(screen.getByRole("textbox", { name: "Key 1" })).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("textbox", { name: "Key 2" })).toHaveAttribute("aria-invalid", "true");
  });
});
