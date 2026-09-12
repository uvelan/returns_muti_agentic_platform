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

  it(
    // CFG-7, found running the item-1 live acceptance loop against
    // /config/agents: a real (Playwright) click on the visible switch
    // graphic hit the decorative `aria-hidden` span, which used to sit
    // outside the `<label>` -- only the text after it was wrapped. jsdom's
    // click simulation does not model pointer-event interception the way a
    // real browser does, so this test cannot reproduce THAT failure mode
    // directly, but it does pin the fix's actual mechanism: the switch
    // graphic must be a `<label>` descendant so a browser forwards its
    // click to the input regardless of what visually overlaps what.
    "toggles when the switch graphic itself is clicked, not only the text",
    async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const { container } = render(<Toggle label="Enabled" value={false} onChange={onChange} />);
      const graphic = container.querySelector('span[aria-hidden="true"]');
      expect(graphic).not.toBeNull();
      // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
      await user.click(graphic!);
      expect(onChange).toHaveBeenCalledWith(true);
    },
  );

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
