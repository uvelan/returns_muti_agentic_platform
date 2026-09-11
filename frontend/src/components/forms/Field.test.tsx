import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Field } from "./Field";

describe("Field", () => {
  it("gives the control a programmatic name from the visible label", () => {
    render(
      <Field label="Max bays" htmlFor="max-bays">
        {(control) => <input {...control} />}
      </Field>,
    );
    expect(screen.getByRole("textbox", { name: "Max bays" })).toBeInTheDocument();
  });

  it("marks a required field without hiding the marker from assistive tech", () => {
    render(
      <Field label="Max bays" htmlFor="max-bays" required>
        {(control) => <input {...control} />}
      </Field>,
    );
    // The accessible name still includes the visible "*" glyph's sr-only
    // companion text, so "required" reaches a screen reader.
    expect(screen.getByRole("textbox", { name: /Max bays/ })).toBeInTheDocument();
    expect(screen.getByText("required")).toHaveClass("sr-only");
  });

  it("wires the hint into aria-describedby", () => {
    render(
      <Field label="Max bays" htmlFor="max-bays" hint="Between 1 and 50">
        {(control) => <input {...control} />}
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Max bays" });
    const describedBy = input.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)).toHaveTextContent("Between 1 and 50");
  });

  it("marks the control invalid and announces the error via aria-describedby and role=alert", () => {
    render(
      <Field label="Max bays" htmlFor="max-bays" error="Must be a whole number">
        {(control) => <input {...control} />}
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Max bays" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    const describedBy = input.getAttribute("aria-describedby") ?? "";
    const errorNode = document.getElementById(describedBy);
    expect(errorNode).toHaveTextContent("Must be a whole number");
    expect(errorNode).toHaveAttribute("role", "alert");
  });

  it("describes both hint and error together when both are present", () => {
    render(
      <Field label="Max bays" htmlFor="max-bays" hint="1-50" error="Out of range">
        {(control) => <input {...control} />}
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Max bays" });
    const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toHaveLength(2);
    expect(ids.map((id) => document.getElementById(id)?.textContent)).toEqual(["1-50", "Out of range"]);
  });

  it("accepts a plain node child, associated with the label by id", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Field label="Enabled" htmlFor="enabled">
        <input type="checkbox" id="enabled" onChange={onChange} />
      </Field>,
    );
    const checkbox = screen.getByRole("checkbox", { name: "Enabled" });
    await user.click(checkbox);
    expect(onChange).toHaveBeenCalledOnce();
  });
});
