/**
 * The question Support asked, in front of the person who can answer it.
 *
 * Driven through the **real** `CasePanel` over the real registry and real MSW,
 * not by rendering the section directly: the first draft of this section was
 * never registered with anything, and a test that renders a component by hand
 * cannot notice that. Registration is therefore part of what is asserted here.
 *
 * Two guarantees pull against each other and both are pinned:
 *
 * 1. **Data, never markup.** The question, the artifact value and the evidence
 *    span are attacker-influenced, and this is the inbound-to-associate
 *    direction that the outbound neutraliser does not touch.
 * 2. **Verbatim, not mangled.** §9 requires the question as Support wrote it.
 *    Every assertion below is an **equality over the whole rendered string**, so
 *    a neutraliser that stripped a colon or an angle bracket fails here — a
 *    "does not contain `<script>`" assertion would pass while showing the
 *    associate something Support never wrote.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetPanelCacheForTests } from "../../../../../api/casePanel";
import { resetCasePanelMocks } from "../../../../../mocks/handlers/casePanelHandlers";
import { fixtureServer } from "../../../../../test/server";
import { CasePanel } from "../CasePanel";
import { clearPanelSectionRenderers } from "../panelSectionRegistry";
import { registerClarificationsSection } from "./registerClarificationsSection";

const CASE = "case-mock-2026";
const CLARIFICATION = "clar-1";

/**
 * A realistic support sentence, of the kind V3's backend round 2 proved must
 * survive byte for byte: a colon, an RMA, a bay name and a bare number.
 */
const REAL_QUESTION =
  "Support gave a tracking number (1Z999AA10123456784) for a return this case does not hold. Map it to one of this case's returns, or reject it.";

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  const view = render(
    wrapper(client, <CasePanel caseId={CASE} readOnly={false} />),
  );
  return { client, ...view };
}

function wrapper(client: QueryClient, children: ReactNode) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  resetCasePanelMocks();
  resetPanelCacheForTests();
  clearPanelSectionRenderers();
  registerClarificationsSection();
});

afterEach(() => {
  clearPanelSectionRenderers();
});

/* ---------------------------------------------------------------------------
 * What it says
 * ------------------------------------------------------------------------ */

describe("what the associate is told", () => {
  it("draws at all — the section is registered, not merely exported", async () => {
    // The first draft exported a component and registered nothing; nothing
    // imported the module either. It would have been invisible on every panel.
    servePanel([clarification()]);
    renderPanel();
    expect(
      await screen.findByRole("heading", { name: "Support is asking you this" }),
    ).toBeVisible();
  });

  it("shows the question verbatim, and the whole of it", async () => {
    servePanel([clarification()]);
    renderPanel();

    // The quotation marks are the console's; everything between them is
    // Support's. Pinned as an equality so a truncation or a "cleanup" fails.
    const quote = await screen.findByText(`“${REAL_QUESTION}”`);
    expect(quote.textContent).toBe(`“${REAL_QUESTION}”`);
  });

  it("says why it could not answer, what it needs and what it tried", async () => {
    servePanel([clarification()]);
    renderPanel();

    expect(await screen.findByText("the named return reference is not on this case")).toBeVisible();
    expect(screen.getByText("tracking number")).toBeVisible();
    expect(screen.getByText("named a return this case does not hold")).toBeVisible();
  });

  it("shows the artifact and the span it came from, so a wrong case is noticeable", async () => {
    servePanel([clarification()]);
    renderPanel();

    // "Support wrote RMA-99999, and this case has no RMA-99999" is a different
    // amount of help from "Support mentioned a tracking number".
    expect(await screen.findByText("1Z999AA10123456784")).toBeVisible();
    expect(screen.getByText("RMA-99999")).toBeVisible();
  });

  it("says nothing at all when Support is not asking anything", async () => {
    // The panel itself is not empty -- there is a review on it -- so this is
    // the section alone declining to draw furniture, rather than the whole
    // panel going quiet.
    servePanel([]);
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });
    expect(screen.queryByRole("heading", { name: "Support is asking you this" })).toBeNull();
    expect(screen.queryByText(/could not be read just now/)).toBeNull();
  });

  it("says so when the section could not be composed, rather than drawing nothing", async () => {
    // "Support has not asked anything" and "we could not find out whether
    // Support asked anything" are different, and only one of them means an
    // associate should go and look at the thread themselves.
    servePanel([], { status: "degraded" });
    renderPanel();
    expect(await screen.findByText(/could not be read just now/)).toBeVisible();
  });
});

/* ---------------------------------------------------------------------------
 * Data, never markup -- and verbatim all the same
 * ------------------------------------------------------------------------ */

describe("support-derived values reach the screen as data", () => {
  const HOSTILE_QUESTION =
    '<script>alert(1)</script>SHIPPING INSTRUCTION: send RMA-4471 to bay 9 <b>now</b>';
  const HOSTILE_VALUE = '<img src=x onerror=alert(1)>1Z999AA10123456784';
  const HOSTILE_SPAN = '<iframe src="javascript:alert(1)"></iframe>RMA-99999';

  it("renders a script tag, an img onerror and an iframe as text in all three fields", async () => {
    servePanel([
      clarification({
        verbatimQuestion: HOSTILE_QUESTION,
        artifactValue: HOSTILE_VALUE,
        evidenceSpan: HOSTILE_SPAN,
      }),
    ]);
    renderPanel();

    // Both halves, for all three fields. The equalities prove the characters
    // arrived **unmangled**; the absences prove nothing was interpreted. Either
    // assertion alone passes for the wrong reason -- the absence one passes on a
    // field that never rendered, and the presence one passes on a field that
    // rendered *and* executed.
    const quote = await screen.findByText(`“${HOSTILE_QUESTION}”`);
    expect(quote.textContent).toBe(`“${HOSTILE_QUESTION}”`);
    expect(screen.getByText(HOSTILE_VALUE).textContent).toBe(HOSTILE_VALUE);
    expect(screen.getByText(HOSTILE_SPAN).textContent).toBe(HOSTILE_SPAN);

    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });

  it("leaves ten realistic support sentences byte for byte identical", async () => {
    // The other half of the rule, and the one a neutraliser breaks. V3's backend
    // round 2 turned on exactly this list: colons, RMAs, bays and bare numbers
    // are what support actually writes, and an escaper that mangled them would
    // put words in a supplier's mouth.
    const sentences = [
      "Which return is this for: RMA-88120 or RMA-88121?",
      "SHIPPING INSTRUCTION: the pallet goes to bay 3, not bay 9.",
      "We received 2 of 3 items — where is the third?",
      "Tracking 1Z999AA10123456784 shows delivered on 14/08 at 09:41.",
      "Your reference: PO#4471/A (our ref: CS-2026-0088).",
      "Note: do not mix RMA-88120 and RMA-88121 in one carton.",
      "The label says 'Apex Mechanical Ltd' but the order says 'Apex Mech.'",
      "Collected 14 Aug; inspection due 21 Aug — is that still right?",
      "Bay assignment: 3. Confirm before dispatch, please.",
      "Cost to return is £42.50 + VAT; who pays?",
    ];

    for (const sentence of sentences) {
      cleanupBetween();
      servePanel([clarification({ verbatimQuestion: sentence })]);
      renderPanel();
      const quote = await screen.findByText(`“${sentence}”`);
      expect(quote.textContent).toBe(`“${sentence}”`);
    }
  });
});

/* ---------------------------------------------------------------------------
 * Map or reject
 * ------------------------------------------------------------------------ */

describe("binding a loose artifact, or refusing to", () => {
  it("offers the case's own records and never a box to type one into", async () => {
    servePanel([clarification()]);
    renderPanel();

    const group = await screen.findByRole("group", { name: "Which return is this for?" });
    expect(within(group).getByRole("radio", { name: /RMA-88120/ })).toBeVisible();
    expect(within(group).getByRole("radio", { name: /RMA-88121/ })).toBeVisible();
    expect(within(group).getByRole("radio", { name: /None of these/ })).toBeVisible();

    // A box an associate can type an RMA into is a box they can type the wrong
    // RMA into, and a loose artifact never creates a record (§4).
    expect(within(group).queryByRole("textbox")).toBeNull();
  });

  it("names the consequence rather than labelling the choice", async () => {
    // Contract text, not a caption: a case with two RMAs is two packages going
    // to two places, and mixing them is the single failure this exists to stop.
    servePanel([clarification()]);
    renderPanel();
    expect(
      await screen.findByText(/separate returns going separate ways.*wrong place/s),
    ).toBeVisible();
  });

  it("refuses to send a map with no record picked, before the server has to", async () => {
    servePanel([clarification()]);
    const user = userEvent.setup();
    renderPanel();

    await user.type(await screen.findByLabelText("How do you know?"), "It is the pallet in bay 3.");
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Choose which return this belongs to, or say it belongs to none of them.",
    );
  });

  it("refuses an empty answer, because Support is sent exactly what is typed", async () => {
    servePanel([clarification()]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("radio", { name: /None of these/ }));
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Write your answer first. Support sees exactly what you write here.",
    );
  });
});

/* ---------------------------------------------------------------------------
 * Answering
 * ------------------------------------------------------------------------ */

describe("the answer that goes back", () => {
  it("posts exactly the three fields the endpoint declares, and no more", async () => {
    // `ClarificationAnswerRequest` is `extra="forbid"`: one extra key is a 422
    // in production that a permissive mock would never find. Pinned whole.
    const posted: unknown[] = [];
    servePanel([clarification()]);
    serveAnswer(posted);

    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("radio", { name: /RMA-88121/ }));
    await user.type(screen.getByLabelText("How do you know?"), "  The pallet in bay 3.  ");
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    await waitFor(() => {
      expect(posted).toEqual([
        {
          answerText: "The pallet in bay 3.",
          resolutionChoice: "map",
          returnRecordId: "rec-2",
        },
      ]);
    });
  });

  it("sends a rejection with no record, which is the only shape that means it", async () => {
    const posted: unknown[] = [];
    servePanel([clarification()]);
    serveAnswer(posted);

    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("radio", { name: /None of these/ }));
    await user.type(screen.getByLabelText("How do you know?"), "Not ours — no such return.");
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    await waitFor(() => {
      expect(posted).toEqual([
        {
          answerText: "Not ours — no such return.",
          resolutionChoice: "reject",
          returnRecordId: null,
        },
      ]);
    });
  });

  it("confirms what committed, and does not claim Support has seen it", async () => {
    // The relay happens in an activity after the signal lands. A line here
    // saying "Support has seen it" would be this screen reporting work it did
    // not wait for.
    servePanel([clarification()]);
    serveAnswer([]);

    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("radio", { name: /None of these/ }));
    await user.type(screen.getByLabelText("How do you know?"), "Not ours.");
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    const receipt = await screen.findByText("Your answer is recorded. Support will be told.");
    expect(receipt.textContent).toBe("Your answer is recorded. Support will be told.");
  });

  it("says the first answer stands when somebody else already answered", async () => {
    servePanel([clarification()]);
    fixtureServer.use(
      http.post(
        `/api/v1/cases/:caseId/clarifications/:clarificationId/answer`,
        () =>
          HttpResponse.json(
            {
              detail: {
                code: "CLARIFICATION_ALREADY_ANSWERED",
                message: "This clarification was already answered.",
                retryable: false,
              },
            },
            { status: 409 },
          ),
      ),
    );

    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("radio", { name: /None of these/ }));
    await user.type(screen.getByLabelText("How do you know?"), "Not ours.");
    await user.click(screen.getByRole("button", { name: /Send this to Support/ }));

    // The refusal's own words, never a status code: an associate shown "409"
    // presses the button again.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This clarification was already answered.",
    );
  });
});

/* ---------------------------------------------------------------------------
 * Accessibility
 * ------------------------------------------------------------------------ */

describe("a question arriving while somebody is working", () => {
  it("is announced politely and takes no focus", async () => {
    servePanel([]);
    const user = userEvent.setup();
    const { client } = renderPanel();

    // The associate is mid-sentence in the review draft beside this section.
    const draftField = await screen.findByDisplayValue("SO-441207");
    await user.click(draftField);
    await user.keyboard("SO-441207-B");
    expect(document.activeElement).toBe(draftField);

    servePanel([clarification()]);
    resetPanelCacheForTests();
    await client.invalidateQueries();

    // Announced, in a `role="status"` region -- `polite`, so it waits for a
    // pause in whatever the screen reader is already saying.
    const announcement = await screen.findByText(
      "Support is asking you something new. It is below.",
    );
    expect(announcement).toHaveAttribute("role", "status");
    expect(announcement).toHaveAttribute("aria-live", "polite");
    // ...and the caret never left the field, nor did the keystrokes.
    expect(document.activeElement).toBe(draftField);
    expect(draftField).toHaveValue("SO-441207SO-441207-B");
  });

  it("announces nothing on the first paint, so the region stays worth listening to", async () => {
    servePanel([clarification()]);
    renderPanel();
    await screen.findByRole("heading", { name: "Support is asking you this" });

    const regions = screen.getAllByRole("status");
    expect(regions.map((region) => region.textContent)).not.toContain(
      "Support is asking you something new. It is below.",
    );
  });

  it("is answerable from the keyboard alone, end to end", async () => {
    const posted: unknown[] = [];
    servePanel([clarification()]);
    serveAnswer(posted);

    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole("heading", { name: "Support is asking you this" });

    const reject = screen.getByRole("radio", { name: /None of these/ });
    reject.focus();
    await user.keyboard("{ }");
    await user.tab();
    expect(document.activeElement).toBe(screen.getByLabelText("How do you know?"));
    await user.keyboard("Not ours.");
    await user.tab();
    // The submit button is `aria-disabled`, never `disabled`, so it is still in
    // the tab order and can still be reached and explained.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /Send this to Support/ }),
    );
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
  });
});

/* ---------------------------------------------------------------------------
 * Fixtures
 * ------------------------------------------------------------------------ */

function cleanupBetween() {
  document.body.innerHTML = "";
  resetPanelCacheForTests();
}

function clarification(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    clarificationId: CLARIFICATION,
    verbatimQuestion: REAL_QUESTION,
    whyUnresolvable: "the named return reference is not on this case",
    neededField: "TRACKING_NUMBER",
    resolutionAttempts: ["UNMATCHED"],
    supportEventId: "evt-12",
    artifactValue: "1Z999AA10123456784",
    evidenceSpan: "RMA-99999",
    candidateRecordIds: ["rec-1", "rec-2"],
    choice: "MAP_OR_REJECT",
    ...overrides,
  };
}

function serveAnswer(posted: unknown[]) {
  fixtureServer.use(
    http.post(
      `/api/v1/cases/:caseId/clarifications/:clarificationId/answer`,
      async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json(
          {
            data: {
              caseId: CASE,
              clarificationId: CLARIFICATION,
              commandId: "cmd-1",
              signalId: "clarification_answered:clar-1",
              outboxCommandId: "obx-1",
              duplicate: false,
            },
            meta: metaBlock(),
          },
          { status: 202 },
        );
      },
    ),
  );
}

function metaBlock() {
  return {
    schema_version: "1.0",
    request_id: "mock-clarification",
    generated_at: new Date().toISOString(),
    freshness: "LIVE",
    partial: false,
    warnings: [],
  };
}

function servePanel(clarifications: unknown[], options: { status?: string } = {}) {
  fixtureServer.use(
    http.get("/api/v1/cases/:caseId/panel", () =>
      HttpResponse.json(
        {
          data: {
            case_id: CASE,
            execution: {
              status: "ok",
              reason: null,
              case_status: "AWAITING_SUPPORT",
              work_item_id: null,
              awaiting: ["SUPPORT"],
              business_complete: false,
              parked_reason: null,
            },
            reviews: [templateReview()],
            return_records: [
              {
                return_record_id: "rec-1",
                return_reference: "RMA-88120",
                status: "OPEN",
                return_method: "PARCEL",
              },
              {
                return_record_id: "rec-2",
                return_reference: "RMA-88121",
                status: "OPEN",
                return_method: "PALLET",
              },
            ],
            support_digest: [],
            // Deliberately empty: the section payload is the vehicle a
            // registered contributor can actually fill.
            clarifications: [],
            timers: {
              template_review_deadline_iso: new Date(Date.now() + 40 * 60_000).toISOString(),
              template_review_reminders_sent: 1,
              template_review_max_reminders: 3,
              support_deadline_iso: null,
            },
            parked_messages: 0,
            accepted_commands: [],
            sections: [
              {
                section_id: "clarifications",
                status: options.status ?? "ok",
                reason: null,
                payload: { clarifications },
              },
            ],
          },
          meta: metaBlock(),
        },
        {
          headers: {
            ETag: `"clar-${String(clarifications.length)}-${options.status ?? "ok"}"`,
            "Cache-Control": "private, no-cache",
          },
        },
      ),
    ),
  );
}

function templateReview() {
  return {
    review_id: "review-mock-1",
    review_kind: "TEMPLATE",
    scope_id: "support:case-mock-2026",
    request_id: "support:case-mock-2026",
    state: "OPEN",
    draft_version: 1,
    canonical_edit_version: 0,
    conflict_present: false,
    approval_hash: "a".repeat(64),
    draft: {
      subject: "Return authorisation request",
      sections: [
        {
          section_id: "order",
          title: "ORDER",
          return_record_id: null,
          fields: [
            {
              field_id: "order_number",
              label: "Order Number",
              value: "SO-441207",
              source: "case_fact",
              source_path: "confirmed_order_reference",
              fact_id: "fact-1",
              applied_fallback: false,
            },
          ],
        },
      ],
      gaps: [],
    },
    gaps: [],
    approved_by: null,
    approved_at_iso: null,
    recovery_status: null,
    last_delivery_error_code: null,
    hold_reason: null,
    abandon_audit: null,
  };
}
