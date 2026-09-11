import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { OrderedList } from "./OrderedList";

type Stage = { id: string; name: string };

function ControlledStages({ initial }: { initial: Stage[] }) {
  const [items, setItems] = useState(initial);
  return (
    <OrderedList
      label="Stage sequence"
      items={items}
      onChange={setItems}
      keyOf={(stage) => stage.id}
      renderItem={(stage) => <span>{stage.name}</span>}
    />
  );
}

const STAGES: Stage[] = [
  { id: "intake", name: "Intake" },
  { id: "inspect", name: "Inspect" },
  { id: "close", name: "Close" },
];

describe("OrderedList", () => {
  it("names the list and renders each item", () => {
    render(<OrderedList label="Stage sequence" items={STAGES} onChange={vi.fn()} keyOf={(s) => s.id} renderItem={(s) => <span>{s.name}</span>} />);
    expect(screen.getByRole("list", { name: "Stage sequence" })).toBeInTheDocument();
    expect(screen.getByText("Intake")).toBeInTheDocument();
    expect(screen.getByText("Close")).toBeInTheDocument();
  });

  it("moves an item down with a labelled, keyboard-operable button", async () => {
    const user = userEvent.setup();
    render(<ControlledStages initial={STAGES} />);
    await user.click(screen.getByRole("button", { name: "Move intake down" }));
    const items = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(items[0]).toContain("Inspect");
    expect(items[1]).toContain("Intake");
  });

  it("disables moving the first item up and the last item down", () => {
    render(<ControlledStages initial={STAGES} />);
    expect(screen.getByRole("button", { name: "Move intake up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move close down" })).toBeDisabled();
  });

  it("is reachable by Tab and Enter alone, with no drag required", async () => {
    const user = userEvent.setup();
    render(<ControlledStages initial={STAGES} />);
    const button = screen.getByRole("button", { name: "Move inspect up" });
    button.focus();
    await user.keyboard("{Enter}");
    const items = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(items[0]).toContain("Inspect");
  });

  // A6 (CFG-3b RV, carried to CFG-4): a list-level error, for a refusal that
  // names the whole sequence rather than one of its items -- an empty
  // required list, an unreachable rung.
  it("shows a list-level error as an alert, and none when no error is given", () => {
    const { rerender } = render(
      <OrderedList label="Stage sequence" items={STAGES} onChange={vi.fn()} keyOf={(s) => s.id} renderItem={(s) => <span>{s.name}</span>} />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(
      <OrderedList
        label="Stage sequence"
        error="At least one stage is required."
        items={STAGES}
        onChange={vi.fn()}
        keyOf={(s) => s.id}
        renderItem={(s) => <span>{s.name}</span>}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("At least one stage is required.");
  });
});
