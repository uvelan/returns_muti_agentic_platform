/**
 * UI-05 -- the contextual rail, and the three ways it could go wrong.
 *
 * It could render a screen's context in the middle of the workspace when there
 * is no rail (a collapsed sidebar, or a screen under test); it could keep
 * rendering stale context after the screen stops publishing it; and it could
 * become navigation, which is the thing it exists instead of.
 */

import { render, screen } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { DomainRail, RailFact, RailNote, RailSection } from "./DomainRail";
import { RailSlotProvider } from "./railSlot";

/** A minimal stand-in for `DomainFrame`: a slot, and a screen beside it. */
function Frame({ withSlot, children }: { withSlot: boolean; children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <div>
      {withSlot ? <aside data-testid="rail" ref={setSlot} /> : null}
      <main data-testid="workspace">
        <RailSlotProvider value={withSlot ? slot : null}>{children}</RailSlotProvider>
      </main>
    </div>
  );
}

describe("DomainRail", () => {
  it("renders the screen's context into the rail, not into the workspace", () => {
    render(
      <Frame withSlot>
        <DomainRail>
          <RailSection title="Queue">
            <RailFact label="Waiting" value={3} />
          </RailSection>
        </DomainRail>
        <p>the screen</p>
      </Frame>,
    );

    const rail = screen.getByTestId("rail");
    expect(rail).toHaveTextContent("Queue");
    expect(rail).toHaveTextContent("Waiting");
    expect(screen.getByTestId("workspace")).toHaveTextContent("the screen");
  });

  it("renders nothing at all when there is no slot", () => {
    // A collapsed rail, or a screen rendered outside the shell. Falling back to
    // rendering in place would drop a sidebar block into the middle of the
    // workspace, which is worse than the block being absent.
    render(
      <Frame withSlot={false}>
        <DomainRail>
          <RailSection title="Queue">
            <RailFact label="Waiting" value={3} />
          </RailSection>
        </DomainRail>
        <p>the screen</p>
      </Frame>,
    );

    expect(screen.queryByText("Queue")).toBeNull();
    expect(screen.getByTestId("workspace")).toHaveTextContent("the screen");
  });

  it("keeps a label whose value is unknown, rather than dropping the row", () => {
    // A missing row and a row reading "none" are different answers, and on a
    // rail the reader cannot tell which one they are looking at without the
    // label staying put.
    render(
      <Frame withSlot>
        <DomainRail>
          <RailSection title="This return">
            <RailFact label="Case" value={null} />
            <RailNote>Nothing is confirmed yet.</RailNote>
          </RailSection>
        </DomainRail>
      </Frame>,
    );

    expect(screen.getByText("Case")).toBeTruthy();
    expect(screen.getByText("--")).toBeTruthy();
    expect(screen.getByText("Nothing is confirmed yet.")).toBeTruthy();
  });

  it("carries no links, because it is context and not navigation", () => {
    render(
      <Frame withSlot>
        <DomainRail>
          <RailSection title="Reachability">
            <RailFact label="Configured" value={4} />
          </RailSection>
        </DomainRail>
      </Frame>,
    );

    // The rail's navigation is the domain's own sections, rendered by the
    // shell. A screen publishing links here would be rebuilding the shared
    // all-domains panel Wave E removed, one screen at a time.
    expect(screen.getByTestId("rail").querySelectorAll("a")).toHaveLength(0);
  });
});
