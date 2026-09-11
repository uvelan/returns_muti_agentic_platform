import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffPreview } from "./DiffPreview";

describe("DiffPreview", () => {
  it("says nothing changed when before and after are the same", () => {
    render(<DiffPreview before={{ enabled: true }} after={{ enabled: true }} />);
    expect(screen.getByText("Nothing changed")).toBeInTheDocument();
  });

  it("shows one row per changed leaf, with before and after values", () => {
    render(
      <DiffPreview
        before={{ enabled: false, disabled_reason: "paused", limits: { max: 5, min: 1 } }}
        after={{ enabled: true, disabled_reason: "paused", limits: { max: 9, min: 1 } }}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "enabled" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "limits.max" })).toBeInTheDocument();
    expect(screen.queryByText("disabled_reason")).not.toBeInTheDocument();
  });

  it("flags a deletion instead of showing it as a value change", () => {
    render(
      <DiffPreview
        before={{ ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } }}
        after={{ ship_via_methods: { CPU: "COUNTER" } }}
      />,
    );
    expect(screen.getByRole("cell", { name: "ship_via_methods.XPW" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Removed" })).toBeInTheDocument();
  });

  it("names the table for assistive tech via a caption", () => {
    render(<DiffPreview before={{ a: 1 }} after={{ a: 2 }} title="Policy evaluation changes" />);
    expect(screen.getByText("Policy evaluation changes")).toBeInTheDocument();
  });
});
