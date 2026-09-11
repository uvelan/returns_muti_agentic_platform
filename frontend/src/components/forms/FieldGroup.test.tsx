import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FieldGroup } from "./FieldGroup";

describe("FieldGroup", () => {
  it("renders the kicker, title, description and children", () => {
    render(
      <FieldGroup kicker="Discovery" title="Order discovery" description="How an order is found.">
        <p>a field</p>
      </FieldGroup>,
    );
    expect(screen.getByText("Discovery")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Order discovery" })).toBeInTheDocument();
    expect(screen.getByText("How an order is found.")).toBeInTheDocument();
    expect(screen.getByText("a field")).toBeInTheDocument();
  });

  it("is not collapsible by default -- content is always visible with no toggle", () => {
    render(
      <FieldGroup kicker="Discovery" title="Order discovery">
        <p>a field</p>
      </FieldGroup>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("a field")).toBeVisible();
  });

  it("collapses and expands with aria-expanded and aria-controls wired to the content", async () => {
    const user = userEvent.setup();
    render(
      <FieldGroup kicker="Discovery" title="Order discovery" collapsible defaultOpen>
        <p>a field</p>
      </FieldGroup>,
    );
    const button = screen.getByRole("button", { name: "Collapse" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    const controlsId = button.getAttribute("aria-controls");
    expect(controlsId).not.toBeNull();
    const region = document.getElementById(controlsId ?? "");
    expect(region).toContainElement(screen.getByText("a field"));

    await user.click(button);
    expect(screen.getByRole("button", { name: "Expand" })).toHaveAttribute("aria-expanded", "false");
    expect(region).not.toBeVisible();
  });

  it("starts collapsed when defaultOpen is false", () => {
    render(
      <FieldGroup kicker="Discovery" title="Order discovery" collapsible defaultOpen={false}>
        <p>a field</p>
      </FieldGroup>,
    );
    expect(screen.getByRole("button", { name: "Expand" })).toHaveAttribute("aria-expanded", "false");
  });
});
