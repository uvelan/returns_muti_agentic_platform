import {
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  beforeEach,
  describe,
  expect,
  it,
} from "vitest";
import { Router } from "wouter";

import { Shell } from "./Shell";

describe("Shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders navigation links and children", () => {
    render(
      <Router>
        <Shell>
          <div data-testid="child-content">Child Content</div>
        </Shell>
      </Router>,
    );

    expect(
      screen.getByLabelText("Return Platform overview"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("child-content"),
    ).toBeInTheDocument();
  });

  it("toggles mobile menu", () => {
    render(
      <Router>
        <Shell>
          <div>Content</div>
        </Shell>
      </Router>,
    );

    const button = screen.getByRole("button", {
      name: /open menu/i,
    });

    expect(button).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    fireEvent.click(button);

    expect(button).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(
      screen.getByRole("button", {
        name: /close menu/i,
      }),
    ).toBeInTheDocument();
  });

  it("collapses the desktop sidebar and persists preference", () => {
    render(
      <Router>
        <Shell>
          <div>Content</div>
        </Shell>
      </Router>,
    );

    const sidebar = screen.getByLabelText(
      "Primary navigation",
    );
    const collapseButton = screen.getByRole("button", {
      name: /collapse sidebar/i,
    });

    expect(sidebar).toHaveAttribute(
      "data-collapsed",
      "false",
    );
    expect(collapseButton).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    fireEvent.click(collapseButton);

    expect(sidebar).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    expect(
      screen.getByRole("button", {
        name: /expand sidebar/i,
      }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      window.localStorage.getItem(
        "return-platform.desktop-sidebar-collapsed",
      ),
    ).toBe("true");
  });

  it("restores a collapsed desktop sidebar preference", () => {
    window.localStorage.setItem(
      "return-platform.desktop-sidebar-collapsed",
      "true",
    );

    render(
      <Router>
        <Shell>
          <div>Content</div>
        </Shell>
      </Router>,
    );

    expect(
      screen.getByLabelText("Primary navigation"),
    ).toHaveAttribute("data-collapsed", "true");
    expect(
      screen.getByRole("button", {
        name: /expand sidebar/i,
      }),
    ).toBeInTheDocument();
  });
});
