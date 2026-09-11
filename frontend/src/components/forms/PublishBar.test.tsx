import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PublishBar } from "./PublishBar";

describe("PublishBar", () => {
  it("says nothing is staged and disables Publish when dirtyCount is 0", () => {
    render(<PublishBar dirtyCount={0} onPublish={vi.fn()} />);
    expect(screen.getByText("Nothing staged yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("shows the staged count and enables Publish once there is something to send", async () => {
    const user = userEvent.setup();
    const onPublish = vi.fn();
    render(<PublishBar dirtyCount={3} onPublish={onPublish} />);
    expect(screen.getByText("3 changes staged.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Publish" });
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onPublish).toHaveBeenCalledOnce();
  });

  it("disables Publish with a visible reason regardless of dirty count", () => {
    render(<PublishBar dirtyCount={2} onPublish={vi.fn()} disabledReason="config.release.promote is required" />);
    const button = screen.getByRole("button", { name: "Publish" });
    expect(button).toBeDisabled();
    expect(screen.getByText("config.release.promote is required")).toBeInTheDocument();
  });

  // A7 (CFG-3b RV, carried to CFG-4): `title` on a disabled button is not
  // reliably announced -- the visible reason now also reaches the button
  // through `aria-describedby`, not only through a sibling `<span>`.
  it("wires the disabled reason to the Publish button through aria-describedby", () => {
    render(<PublishBar dirtyCount={2} onPublish={vi.fn()} disabledReason="config.release.promote is required" />);
    const button = screen.getByRole("button", { name: "Publish" });
    const describedBy = button.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "config.release.promote is required",
    );
  });

  it("leaves Publish with no aria-describedby when there is no disabled reason", () => {
    render(<PublishBar dirtyCount={2} onPublish={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Publish" })).not.toHaveAttribute("aria-describedby");
  });

  it("offers Validate only when onValidate is given, disabled until something is staged", () => {
    const { rerender } = render(<PublishBar dirtyCount={0} onPublish={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Validate/ })).not.toBeInTheDocument();

    rerender(<PublishBar dirtyCount={0} onPublish={vi.fn()} onValidate={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Validate" })).toBeDisabled();

    rerender(<PublishBar dirtyCount={1} onPublish={vi.fn()} onValidate={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Validate" })).toBeEnabled();
  });

  it("shows the publish steps and a publishing label", () => {
    render(
      <PublishBar
        dirtyCount={1}
        onPublish={vi.fn()}
        publishing
        steps={[{ name: "Create draft release r-1", state: "RUNNING" }]}
      />,
    );
    expect(screen.getByRole("button", { name: "Publishing..." })).toBeDisabled();
    expect(screen.getByText("Create draft release r-1")).toBeInTheDocument();
  });

  it("shows a publish error as an alert", () => {
    render(<PublishBar dirtyCount={1} onPublish={vi.fn()} error="Domain patch refused" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Domain patch refused");
  });
});
