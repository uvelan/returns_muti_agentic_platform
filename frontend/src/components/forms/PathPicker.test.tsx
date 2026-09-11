import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { PathPicker } from "./PathPicker";

const PATHS = ["case_fact:confirmed_order_reference", "graph:order.status"];

function ControlledPathPicker() {
  const [value, setValue] = useState("");
  return <PathPicker label="Source path" value={value} onChange={setValue} paths={PATHS} />;
}

describe("PathPicker", () => {
  it("names the text field and offers the schema's paths as suggestions", () => {
    render(<PathPicker label="Source path" value="" onChange={vi.fn()} paths={PATHS} />);
    const input = screen.getByRole("combobox", { name: "Source path" });
    const listId = input.getAttribute("list");
    expect(listId).not.toBeNull();
    const list = document.getElementById(listId ?? "");
    expect(list?.querySelectorAll("option")).toHaveLength(2);
  });

  it("accepts free text not present in the schema's paths", async () => {
    const user = userEvent.setup();
    render(<ControlledPathPicker />);
    const input = screen.getByRole("combobox", { name: "Source path" });
    await user.type(input, "static:unlisted_value");
    expect(input).toHaveValue("static:unlisted_value");
  });

  it("shows a hint and error the same way every other field does", () => {
    render(
      <PathPicker
        label="Source path"
        value="bad:path"
        onChange={vi.fn()}
        paths={PATHS}
        hint="Where this fact is read from"
        error="Unknown binding kind"
      />,
    );
    const input = screen.getByRole("combobox", { name: "Source path" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    const describedBy = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(describedBy).toHaveLength(2);
  });
});
