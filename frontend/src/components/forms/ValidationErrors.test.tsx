import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ValidationErrors } from "./ValidationErrors";

describe("ValidationErrors", () => {
  it("renders nothing when there are no errors", () => {
    const { container } = render(<ValidationErrors errors={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces the list as an alert and shows the count", () => {
    render(
      <ValidationErrors
        errors={[
          { path: "policy_evaluation.enabled", message: "must be a boolean" },
          { path: "workflow.stages", message: "must not be empty" },
        ]}
      />,
    );
    const region = screen.getByRole("alert");
    expect(region).toHaveTextContent("Validation errors (2)");
    expect(screen.getByText("policy_evaluation.enabled")).toBeInTheDocument();
    expect(screen.getByText("must not be empty")).toBeInTheDocument();
  });

  it("jumps to a field when a row is clicked, and shows plain rows without onJump", async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    render(
      <ValidationErrors
        errors={[{ path: "workflow.stages", message: "must not be empty" }]}
        onJump={onJump}
      />,
    );
    await user.click(screen.getByRole("button", { name: /workflow.stages/ }));
    expect(onJump).toHaveBeenCalledWith("workflow.stages");
  });

  it("shows a plain, non-interactive row when onJump is not given", () => {
    render(<ValidationErrors errors={[{ path: "workflow.stages", message: "must not be empty" }]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
