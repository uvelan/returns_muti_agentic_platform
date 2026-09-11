import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TagListInput } from "./TagListInput";

function ControlledTagListInput({ initial }: { initial: string[] }) {
  const [values, setValues] = useState(initial);
  return <TagListInput label="Ship via codes" values={values} onChange={setValues} />;
}

describe("TagListInput", () => {
  it("names the text field after the label", () => {
    render(<TagListInput label="Ship via codes" values={[]} onChange={vi.fn()} />);
    expect(screen.getByRole("textbox", { name: "Ship via codes" })).toBeInTheDocument();
  });

  it("adds a tag on Enter and clears the draft", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={[]} />);
    const input = screen.getByRole("textbox", { name: "Ship via codes" });
    await user.type(input, "CPU{Enter}");
    expect(screen.getByRole("button", { name: /^CPU\./ })).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("adds a tag on comma", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={[]} />);
    const input = screen.getByRole("textbox", { name: "Ship via codes" });
    await user.type(input, "XPW,");
    expect(screen.getByRole("button", { name: /^XPW\./ })).toBeInTheDocument();
  });

  it("does not add a duplicate or an empty tag", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={["CPU"]} />);
    const input = screen.getByRole("textbox", { name: "Ship via codes" });
    await user.type(input, "CPU{Enter}");
    expect(screen.getAllByRole("button", { name: /^CPU\./ })).toHaveLength(1);
    await user.type(input, "{Enter}");
    expect(screen.getAllByRole("button", { name: /^CPU\./ })).toHaveLength(1);
  });

  it("each chip names its position and how to operate it, and removes on click", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={["CPU", "XPW"]} />);
    const chip = screen.getByRole("button", { name: /^CPU\. Position 1 of 2\./ });
    await user.click(chip);
    expect(screen.queryByRole("button", { name: /^CPU\./ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^XPW\./ })).toBeInTheDocument();
  });

  it("reorders a chip with Alt+ArrowRight", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={["CPU", "XPW"]} />);
    const chip = screen.getByRole("button", { name: /^CPU\. Position 1 of 2\./ });
    chip.focus();
    await user.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(screen.getByRole("button", { name: /^XPW\. Position 1 of 2\./ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^CPU\. Position 2 of 2\./ })).toBeInTheDocument();
  });

  it("removes the last tag on Backspace from an empty draft", async () => {
    const user = userEvent.setup();
    render(<ControlledTagListInput initial={["CPU"]} />);
    const input = screen.getByRole("textbox", { name: "Ship via codes" });
    input.focus();
    await user.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: /^CPU\./ })).not.toBeInTheDocument();
  });

  it("offers suggestions without limiting free text", () => {
    render(
      <TagListInput label="Ship via codes" values={[]} onChange={vi.fn()} suggestions={["CPU", "XPW"]} />,
    );
    const input = screen.getByRole("combobox", { name: "Ship via codes" });
    expect(input).toHaveAttribute("list");
  });
});
