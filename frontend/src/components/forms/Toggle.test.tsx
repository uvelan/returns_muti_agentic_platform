import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Toggle } from "./Toggle";

describe("Toggle", () => {
  it("names the switch after the visible label, not Yes/No", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle label="Enabled" value={false} onChange={onChange} />);
    const checkbox = screen.getByRole("checkbox", { name: "Enabled" });
    await user.click(checkbox);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("is keyboard operable via Space", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle label="Enabled" value={false} onChange={onChange} />);
    const checkbox = screen.getByRole("checkbox", { name: "Enabled" });
    checkbox.focus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("describes the control with its hint", () => {
    render(<Toggle label="Enabled" hint="Applies to new cases only" value={false} onChange={vi.fn()} />);
    const checkbox = screen.getByRole("checkbox", { name: "Enabled" });
    const describedBy = checkbox.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("Applies to new cases only");
  });

  it("shows no reason field when the model does not require one", () => {
    render(<Toggle label="Enabled" value={false} onChange={vi.fn()} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  function EvaluationToggle() {
    const [value, setValue] = useState(true);
    const [reason, setReason] = useState("");
    return (
      <Toggle
        label="Policy evaluation"
        value={value}
        onChange={setValue}
        reasonField={{ value: reason, onChange: setReason, requiredWhen: "off" }}
      />
    );
  }

  it("shows a required, labelled reason field only once the required state is reached, and flags it invalid while empty", async () => {
    const user = userEvent.setup();
    render(<EvaluationToggle />);
    expect(screen.queryByRole("textbox", { name: "Reason" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Policy evaluation" }));
    const reasonInput = screen.getByRole("textbox", { name: "Reason" });
    expect(reasonInput).toBeRequired();
    expect(reasonInput).toHaveAttribute("aria-invalid", "true");
    const describedBy = reasonInput.getAttribute("aria-describedby") ?? "";
    const errorNode = document.getElementById(describedBy);
    expect(errorNode).toHaveTextContent(/reason is required/i);
    expect(errorNode).toHaveAttribute("role", "alert");

    await user.type(reasonInput, "Undecidable on this dev host");
    expect(reasonInput).not.toHaveAttribute("aria-invalid");
  });
});
