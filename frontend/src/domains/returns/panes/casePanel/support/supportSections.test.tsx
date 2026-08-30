import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { CasePanelView, PanelSectionView } from "../../../../../api/casePanel";
import {
  clearPanelSectionRenderers,
  panelSectionRenderers,
} from "../panelSectionRegistry";
import {
  registerSupportPanelSections,
  resetSupportPanelSectionInstall,
} from "./installSupportSections";
import { SUPPORT_SECTION_IDS } from "./supportPanelPayloads";
import {
  SupportAnnouncerSection,
  SupportDigestSection,
  SupportParkedSection,
  SupportRecordsSection,
} from "./supportSections";

/**
 * The sections, against the DOM.
 *
 * This is where dispatch condition 10's **markup** half is proved: a
 * support-derived value must reach the screen as characters and not as an
 * element. `supportPanelPayloads.test.ts` proves the layout half -- the value
 * survives, whitespace-collapsed -- and the two are deliberately separate,
 * because each one alone has a green-but-blind reading. "No `<img>` in the
 * document" passes against a value that never rendered; "the string came back
 * from the reader" passes against a component that then interpreted it.
 *
 * Every assertion about an injected value therefore pins **both**: the literal
 * text present in the rendered tree, and the element absent from it.
 */

const HOSTILE = "<img src=x onerror=alert(1)>ACME <b>Ltd</b>";
const FRAMED = "9Z41\nRETURN LOCATION: dock four";
const FRAMED_FLAT = "9Z41 RETURN LOCATION: dock four";

function panelWith(
  sections: readonly PanelSectionView[],
  extra: Partial<CasePanelView> = {},
): CasePanelView {
  return {
    case_id: "case-1",
    execution: {
      status: "ok",
      reason: null,
      case_status: "OPEN",
      work_item_id: null,
      awaiting: [],
      business_complete: false,
      parked_reason: null,
    },
    reviews: [],
    return_records: [],
    support_digest: [],
    clarifications: [],
    timers: {
      template_review_deadline_iso: null,
      template_review_reminders_sent: 0,
      template_review_max_reminders: 0,
      support_deadline_iso: null,
    },
    parked_messages: 0,
    accepted_commands: [],
    sections,
    ...extra,
  };
}

function section(sectionId: string, payload: Record<string, unknown>, status = "ok"): PanelSectionView {
  return { section_id: sectionId, payload, status, reason: null };
}

const RECORD_ONE = {
  return_record_id: "rec-1",
  return_reference: "the first return",
  status: "OPEN",
  return_method: "PARCEL",
};
const RECORD_TWO = {
  return_record_id: "rec-2",
  return_reference: "the second return",
  status: "OPEN",
  return_method: "PARCEL",
};

function renderRecords(payload: Record<string, unknown>, panel: Partial<CasePanelView> = {}) {
  const contributed = section(SUPPORT_SECTION_IDS.records, payload);
  const view = panelWith([contributed], panel);
  return render(
    <SupportRecordsSection section={contributed} panel={view} caseId="case-1" />,
  );
}

afterEach(() => {
  clearPanelSectionRenderers();
  resetSupportPanelSectionInstall();
});

describe("a support-derived value, on the screen", () => {
  it("draws markup as characters and not as elements", () => {
    renderRecords(
      {
        records: [
          { return_record_id: "rec-1", artifacts: [{ artifact_type: "TRACKING", value: HOSTILE }] },
        ],
      },
      { return_records: [RECORD_ONE] },
    );

    // Both halves. The literal reached the DOM as text...
    expect(screen.getByText(HOSTILE)).toBeVisible();
    // ...and nothing in it was interpreted. Either assertion alone is green
    // against the wrong implementation: the first against a component that
    // rendered the text *and* an element beside it, the second against one that
    // rendered nothing at all.
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });

  it("cannot draw itself an extra labelled row", () => {
    renderRecords(
      {
        records: [
          { return_record_id: "rec-1", artifacts: [{ artifact_type: "TRACKING", value: FRAMED }] },
        ],
      },
      { return_records: [RECORD_ONE] },
    );

    // The card has exactly the rows the platform wrote -- the tracking artifact
    // and the record's method -- and the value's own "RETURN LOCATION:" line is
    // inside one of them rather than beside it. Pinned as the full list of
    // terms, because "there is no term called RETURN LOCATION" would also pass
    // against a card that drew no rows at all.
    expect(screen.getAllByRole("term").map((node) => node.textContent)).toEqual([
      "Tracking",
      "Method",
    ]);
    const values = screen.getAllByRole("definition").map((node) => node.textContent);
    expect(values).toEqual([FRAMED_FLAT, "PARCEL"]);
  });

  it("shows an unrecognised artifact kind under its own name rather than hiding it", () => {
    renderRecords(
      {
        records: [
          {
            return_record_id: "rec-1",
            artifacts: [{ artifact_type: "SOMETHING_NEW", value: "from a newer server" }],
          },
        ],
      },
      { return_records: [RECORD_ONE] },
    );
    expect(screen.getByText("SOMETHING_NEW")).toBeVisible();
    expect(screen.getByText("from a newer server")).toBeVisible();
  });
});

describe("the return-record cards", () => {
  it("gives each return its own heading, so a fan-out can be told apart", () => {
    renderRecords(
      { records: [], framing_prompt_key: "support-multi-record-do-not-mix" },
      { return_records: [RECORD_ONE, RECORD_TWO] },
    );
    expect(screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent)).toEqual([
      "the first return",
      "the second return",
    ]);
  });

  it("warns not to mix records, and only when there is more than one", () => {
    const { unmount } = renderRecords(
      { framing_prompt_key: "support-multi-record-do-not-mix" },
      { return_records: [RECORD_ONE, RECORD_TWO] },
    );
    expect(screen.getByText(/Each card below is a separate return/)).toBeVisible();
    unmount();

    // One record is not a fan-out, and a standing warning that is always there
    // is a warning nobody reads on the day it matters.
    renderRecords(
      { framing_prompt_key: "support-multi-record-do-not-mix" },
      { return_records: [RECORD_ONE] },
    );
    expect(screen.queryByText(/Each card below is a separate return/)).toBeNull();
  });

  it("still warns when the release names a framing this console has not shipped", () => {
    // The failure this guards: a release names a key, the console does not
    // recognise it, and the do-not-mix warning silently disappears from a
    // fan-out -- which is the one case the warning exists for.
    renderRecords(
      { framing_prompt_key: "a-framing-from-a-later-release" },
      { return_records: [RECORD_ONE, RECORD_TWO] },
    );
    expect(screen.getByText(/Each card below is a separate return/)).toBeVisible();
  });

  it("draws the bay once for the case, not once per return", () => {
    renderRecords(
      {
        placement: { facility_id: "the north site", bay_id: "the far aisle", reason: "oversize" },
      },
      { return_records: [RECORD_ONE, RECORD_TWO] },
    );
    // One placement heading, whatever the number of returns. Repeating it would
    // read as one bay per return, which the platform does not claim.
    expect(screen.getAllByText("the far aisle")).toHaveLength(1);
    expect(screen.getByText("the north site")).toBeVisible();
  });

  it("says an artifact could not be filed, and does not pretend it was applied", () => {
    renderRecords(
      {
        unbound: [
          {
            artifact_type: "RMA",
            value: "a reference nobody can place",
            status: "AMBIGUOUS",
            evidence_span: "as   they   wrote   it",
          },
        ],
      },
      { return_records: [RECORD_ONE] },
    );
    expect(screen.getByText(/Nothing has been applied/)).toBeVisible();
    expect(screen.getByText("a reference nobody can place")).toBeVisible();
    // The chip carries its word, not only its colour, and the evidence span is
    // collapsed like every other support-derived value.
    expect(screen.getByText("AMBIGUOUS")).toBeVisible();
    expect(screen.getByText(/as they wrote it/)).toBeVisible();
  });

  it("says nothing at all when Support has sent nothing", () => {
    const { container } = renderRecords({}, { return_records: [] });
    expect(container).toBeEmptyDOMElement();
  });

  it("tells a section it could not read from a case with nothing to say", () => {
    const degraded = section(SUPPORT_SECTION_IDS.records, {}, "degraded");
    render(
      <SupportRecordsSection
        section={degraded}
        panel={panelWith([degraded], { return_records: [RECORD_ONE] })}
        caseId="case-1"
      />,
    );
    // The distinction the backend registry's catch exists to preserve: "nothing
    // to show" and "we could not read what Support told us" must not draw the
    // same, and only one of them is a reason to go and ask somebody.
    expect(screen.getByText(/could not be loaded just now/)).toBeVisible();
    expect(screen.getByText(/nothing Support sent has been lost/)).toBeVisible();
  });
});

describe("the parked-messages entry", () => {
  function renderParked(payload: Record<string, unknown>, parkedMessages = 0) {
    const contributed = section(SUPPORT_SECTION_IDS.parked, payload);
    return render(
      <SupportParkedSection
        section={contributed}
        panel={panelWith([contributed], { parked_messages: parkedMessages })}
        caseId="case-1"
      />,
    );
  }

  it("names the count and says the messages are safe", () => {
    renderParked({ count: 3, nl_enabled: false, quota: 50 });
    expect(screen.getByRole("heading", { level: 3 }).textContent).toContain(
      "3 messages from Support are on file and not yet read",
    );
    expect(screen.getByText(/switched off for this platform/)).toBeVisible();
    expect(screen.getByText(/Nothing has been lost/)).toBeVisible();
    expect(screen.getByText(/This return can hold 50/)).toBeVisible();
  });

  it("asserts no cause when the contributor did not give one", () => {
    // "These are on file" is true either way. "Intake is switched off" is a
    // claim about a release this console has not read.
    renderParked({ count: 1 });
    expect(screen.getByRole("heading", { level: 3 }).textContent).toContain(
      "1 message from Support is on file",
    );
    expect(screen.queryByText(/switched off for this platform/)).toBeNull();
    expect(screen.getByText(/kept until the platform can read them/)).toBeVisible();
  });

  it("says nothing when nothing is parked", () => {
    const { container } = renderParked({ count: 0 });
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the thread digest", () => {
  function renderDigest(payload: Record<string, unknown>) {
    const contributed = section(SUPPORT_SECTION_IDS.digest, payload);
    return render(
      <SupportDigestSection
        section={contributed}
        panel={panelWith([contributed])}
        caseId="case-1"
      />,
    );
  }

  it("shows each message and how many of the thread it is showing", () => {
    renderDigest({
      messages: [
        {
          support_event_id: "evt-1",
          sender_display_name: "the support desk",
          status: "PROCESSED",
          intent: "rma_issued",
          preview: HOSTILE,
        },
      ],
      total: 23,
    });
    expect(screen.getByText(HOSTILE)).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("PROCESSED")).toBeVisible();
    expect(screen.getByText(/Showing 1 of 23/)).toBeVisible();
  });

  it("claims no total when the contributor did not give one", () => {
    renderDigest({ messages: [{ support_event_id: "evt-1", preview: "hello" }] });
    expect(screen.queryByText(/Showing/)).toBeNull();
  });
});

describe("what arrives while somebody is typing", () => {
  function announcer(panel: CasePanelView) {
    return <SupportAnnouncerSection section={undefined} panel={panel} caseId="case-1" />;
  }

  const quiet = panelWith([section(SUPPORT_SECTION_IDS.records, { records: [] })]);
  const arrived = panelWith([
    section(SUPPORT_SECTION_IDS.records, {
      records: [{ return_record_id: "rec-1", artifacts: [{ artifact_type: "RMA", value: "x" }] }],
    }),
  ]);

  it("says nothing about what was already on the case when the screen opened", async () => {
    render(announcer(arrived));
    const region = screen.getByRole("status");
    // A reader landing on a case must not be told that everything already on it
    // has "just arrived".
    await waitFor(() => {
      expect(region.textContent).toBe("");
    });
  });

  it("announces an artifact that arrives, politely, and takes no focus", async () => {
    // The mid-edit rule: the panel polls every ten seconds, so this lands
    // between two keystrokes. The caret must not move.
    const typing = document.createElement("input");
    document.body.append(typing);
    typing.focus();

    const { rerender } = render(announcer(quiet));
    rerender(announcer(arrived));

    const region = await screen.findByRole("status");
    await waitFor(() => {
      expect(region.textContent).toBe("Support has sent something new about this return.");
    });
    expect(region.getAttribute("aria-live")).toBe("polite");
    // Never `assertive`: an associate composing a message to a supplier is not
    // interrupted by a tracking number arriving.
    expect(region.getAttribute("aria-live")).not.toBe("assertive");
    expect(document.activeElement).toBe(typing);
    // And the *property*, not only this scenario's outcome. `activeElement`
    // alone is green against a component that calls `.focus()` on the region,
    // because a `<p>` with no `tabindex` cannot take focus and the call is
    // silently inert -- so the test would be proving the element type rather
    // than the behaviour, and the day somebody adds `tabIndex={-1}` "so the
    // announcement is reachable" it would go on passing. The region must not be
    // focusable in the first place.
    expect(region.hasAttribute("tabindex")).toBe(false);
    expect(document.querySelectorAll("[role=status][tabindex]")).toHaveLength(0);
    typing.remove();
  });

  it("announces a message being parked as its own event", async () => {
    const before = panelWith([], { parked_messages: 0 });
    const after = panelWith([], { parked_messages: 1 });
    const { rerender } = render(announcer(before));
    rerender(announcer(after));
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toBe(
        "A message from Support is on file and not yet read.",
      );
    });
  });

  it("says nothing when an artifact is filed away", async () => {
    // A count going down is not news worth reading aloud mid-sentence.
    const { rerender } = render(announcer(arrived));
    rerender(announcer(quiet));
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toBe("");
    });
  });
});

describe("registration", () => {
  beforeEach(() => {
    clearPanelSectionRenderers();
    resetSupportPanelSectionInstall();
  });

  it("puts four sections into V1's registry, in reading order", () => {
    registerSupportPanelSections();
    expect(panelSectionRenderers().map((renderer) => renderer.sectionId)).toEqual([
      SUPPORT_SECTION_IDS.announcer,
      SUPPORT_SECTION_IDS.parked,
      SUPPORT_SECTION_IDS.records,
      SUPPORT_SECTION_IDS.digest,
    ]);
  });

  it("refuses a second registration of the same id, which is what the guard is for", () => {
    registerSupportPanelSections();
    // The registry's rule, not this module's: two renderers for one id would
    // race and which won would depend on import order.
    expect(() => {
      registerSupportPanelSections();
    }).toThrow(/already registered/);
  });
});
