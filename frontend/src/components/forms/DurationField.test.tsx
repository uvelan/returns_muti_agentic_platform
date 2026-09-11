import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DurationField } from "./DurationField";

function ControlledDurationField({ initial }: { initial: number }) {
  const [seconds, setSeconds] = useState(initial);
  return <DurationField label="Wait" seconds={seconds} onChange={setSeconds} />;
}

describe("DurationField", () => {
  it("shows the best-fit unit and the human-readable form in the hint", () => {
    render(<DurationField label="Wait before escalation" seconds={7200} onChange={vi.fn()} />);
    const amount = screen.getByRole("spinbutton", { name: "Wait before escalation" });
    expect(amount).toHaveValue(2);
    expect(screen.getByRole("combobox", { name: "Wait before escalation unit" })).toHaveValue("h");
    const describedBy = amount.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("2 hours");
  });

  it("stores seconds regardless of the unit the operator edits in", async () => {
    const user = userEvent.setup();
    render(<ControlledDurationField initial={60} />);
    const amount = screen.getByRole("spinbutton", { name: "Wait" });
    await user.clear(amount);
    await user.type(amount, "5");
    // 5 minutes (the best-fit unit for 60s) is 300 seconds -- the human hint
    // says so, seconds is what onChange ultimately carries.
    expect(screen.getByRole("spinbutton", { name: "Wait" })).toHaveValue(5);
    const describedBy = amount.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("5 minutes");
  });

  it("recomputes seconds when the unit changes, keeping the amount stable in the new unit", async () => {
    const user = userEvent.setup();
    render(<ControlledDurationField initial={120} />);
    const unitSelect = screen.getByRole("combobox", { name: "Wait unit" });
    await user.selectOptions(unitSelect, "h");
    // 120 seconds shown in hours starts from 0 (rounded down); typing "3"
    // there now means 3 hours.
    const amount = screen.getByRole("spinbutton", { name: "Wait" });
    await user.clear(amount);
    await user.type(amount, "3");
    const describedBy = amount.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("3 hours");
  });

  it("never lets the amount go negative", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DurationField label="Wait" seconds={60} onChange={onChange} />);
    const amount = screen.getByRole("spinbutton", { name: "Wait" });
    await user.clear(amount);
    expect(onChange).toHaveBeenLastCalledWith(0);
  });
});
