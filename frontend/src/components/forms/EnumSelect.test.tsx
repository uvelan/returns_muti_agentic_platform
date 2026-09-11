import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EnumSelect } from "./EnumSelect";

const OPTIONS = [
  { value: "FIFO", label: "First in, first out" },
  { value: "LIFO", label: "Last in, first out" },
];

describe("EnumSelect", () => {
  it("names the combobox and lists the model's options", () => {
    render(<EnumSelect label="Derivation order" value="FIFO" options={OPTIONS} onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Derivation order" });
    expect(select).toHaveValue("FIFO");
    expect(screen.getByRole("option", { name: "Last in, first out" })).toBeInTheDocument();
  });

  it("calls onChange with the selected value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<EnumSelect label="Derivation order" value="FIFO" options={OPTIONS} onChange={onChange} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Derivation order" }), "LIFO");
    expect(onChange).toHaveBeenCalledWith("LIFO");
  });

  it("keeps a value the release holds that the schema no longer lists, rather than silently dropping it", () => {
    render(<EnumSelect label="Derivation order" value="RETIRED_MODE" options={OPTIONS} onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Derivation order" });
    expect(select).toHaveValue("RETIRED_MODE");
    expect(screen.getByRole("option", { name: /RETIRED_MODE/ })).toBeInTheDocument();
  });

  it("flags an unknown value as an error when allowUnknown is false", () => {
    render(
      <EnumSelect
        label="Derivation order"
        value="RETIRED_MODE"
        options={OPTIONS}
        onChange={vi.fn()}
        allowUnknown={false}
      />,
    );
    const select = screen.getByRole("combobox", { name: "Derivation order" });
    expect(select).toHaveAttribute("aria-invalid", "true");
  });
});
