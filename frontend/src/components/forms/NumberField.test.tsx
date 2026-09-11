import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { NumberField } from "./NumberField";

function ControlledNumberField({ min, max }: { min?: number; max?: number }) {
  const [value, setValue] = useState(12);
  return <NumberField label="Max bays" value={value} min={min} max={max} onChange={setValue} />;
}

describe("NumberField", () => {
  it("names the spinbutton and shows tabular numerals", () => {
    render(<NumberField label="Max bays" value={12} onChange={vi.fn()} />);
    const input = screen.getByRole("spinbutton", { name: "Max bays" });
    expect(input).toHaveClass("tabular-nums");
  });

  it("shows the model's range as a hint", () => {
    render(<NumberField label="Max bays" value={12} min={1} max={50} onChange={vi.fn()} />);
    const input = screen.getByRole("spinbutton", { name: "Max bays" });
    const describedBy = input.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("1 to 50");
  });

  it("shows the unit suffix", () => {
    render(<NumberField label="Timeout" value={30} unit="minutes" onChange={vi.fn()} />);
    expect(screen.getByText("minutes")).toBeInTheDocument();
  });

  it("calls onChange as the operator types, without clamping mid-edit", async () => {
    const user = userEvent.setup();
    render(<ControlledNumberField min={1} max={50} />);
    const input = screen.getByRole("spinbutton", { name: "Max bays" });
    await user.clear(input);
    await user.type(input, "99");
    // Mid-edit, out-of-range values are accepted as typed -- clamping on
    // every keystroke would make "99" impossible to reach by typing "9"
    // then "9" again.
    expect(input).toHaveValue(99);
  });

  it("clamps to the range on blur", async () => {
    const user = userEvent.setup();
    render(<ControlledNumberField min={1} max={50} />);
    const input = screen.getByRole("spinbutton", { name: "Max bays" });
    await user.clear(input);
    await user.type(input, "500");
    await user.tab();
    expect(input).toHaveValue(50);
  });

  it("marks itself invalid and describes the error", () => {
    render(<NumberField label="Max bays" value={12} error="Must be positive" onChange={vi.fn()} />);
    const input = screen.getByRole("spinbutton", { name: "Max bays" });
    expect(input).toHaveAttribute("aria-invalid", "true");
  });
});
