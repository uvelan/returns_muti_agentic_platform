/**
 * A gated reply must be **visible and approvable**, not stalled invisibly.
 *
 * The defect this file pins is not hypothetical and was not in the abandoned
 * work: `CasePanelBody` draws every review through `TemplateReviewSection`
 * regardless of kind, and that component renders `payload.subject` and iterates
 * `payload.sections[]`. A `SUPPORT_REPLY` review's payload
 * (`return_support/reply_gating.py`) has neither. Before the fix this suite
 * covers, a gated reply rendered as the heading "Message to Support", a subject
 * of "Pending", and **no body at all** -- beside a Send button.
 *
 * Fault-injection evidence is recorded in the ledger: reverting the kind switch
 * in `TemplateReviewSection.tsx` fails "the reply is on the screen" and "the
 * whole reply text, pinned", and leaves the approval test green -- which is the
 * point. Approvable was never the broken half; *readable* was, and only an
 * assertion on the text catches it.
 *
 * Every text assertion here is an **equality over the whole rendered value**,
 * never a `toContain` and never a "does not contain": a substring assertion
 * passes on a truncated body, and a negative one passes on a body that never
 * rendered.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetPanelCacheForTests } from "../../../../../api/casePanel";
import { resetCasePanelMocks } from "../../../../../mocks/handlers/casePanelHandlers";
import { fixtureServer } from "../../../../../test/server";
import { CasePanel } from "../CasePanel";
import { clearPanelSectionRenderers } from "../panelSectionRegistry";
import {
  confidencePercent,
  isSupportReply,
  readSupportReplyDraft,
  rungWords,
} from "./supportReplyDraft";

const CASE = "case-mock-2026";
const REVIEW = "review-mock-1";
const APPROVAL_HASH = "b".repeat(64);

const REPLY_TEXT =
  "Your return RMA-88120 was collected on 14 August and is in bay 3 awaiting inspection.\n\nThis reply was written by Apex's returns platform.";

function wrapper(client: QueryClient, children: ReactNode) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return { client, ...render(wrapper(client, <CasePanel caseId={CASE} readOnly={false} />)) };
}

beforeEach(() => {
  resetCasePanelMocks();
  resetPanelCacheForTests();
  clearPanelSectionRenderers();
});

afterEach(() => {
  clearPanelSectionRenderers();
});

/* ---------------------------------------------------------------------------
 * The payload, with nothing rendered
 * ------------------------------------------------------------------------ */

describe("reading a reply draft off a review", () => {
  it("returns nothing for a template review, whatever its payload holds", () => {
    // A template review whose payload *happens* to carry a `messageText` key
    // must still draw as a template. The kind decides, never the shape --
    // otherwise a future template field named `messageText` would silently
    // change how every template renders.
    const review = templateReview({ messageText: "not a reply" });
    expect(isSupportReply(review)).toBe(false);
    expect(readSupportReplyDraft(review)).toBeNull();
  });

  it("reads exactly the fields the reply gate writes", () => {
    expect(readSupportReplyDraft(replyReview())).toEqual({
      messageText: REPLY_TEXT,
      disclosesAgent: true,
      intent: "shipment_status",
      resolvedByRung: "case_facts",
      confidenceMillionths: 940_000,
      citedFactIds: ["fact-collected-1", "fact-bay-2"],
      supportEventId: "evt-77",
    });
  });

  it("keeps a reply with an empty body rather than falling back to the template shape", () => {
    // Returning `null` here would send an empty reply back through the renderer
    // that cannot draw it -- the original defect, reached by a different road.
    const draft = readSupportReplyDraft(replyReview({ messageText: "" }));
    expect(draft?.messageText).toBe("");
  });

  it("speaks the ladder's own rung names, and passes an unknown one through", () => {
    // The literal constants in `operations/return_support/resolution_state.py`.
    // The first draft of this module invented `facts` and `tools`, which match
    // nothing the backend writes -- and would have fallen through silently
    // forever, because the fall-through is deliberate.
    expect(rungWords("case_facts")).toBe("from what this case already knows");
    expect(rungWords("graph")).toBe("from the knowledge graph");
    expect(rungWords("registered_tool")).toBe("from a system it is allowed to ask");
    expect(rungWords("a_rung_added_next_year")).toBe("a_rung_added_next_year");
  });

  it("reports the resolver's own confidence, and says nothing when it recorded none", () => {
    expect(confidencePercent(940_000)).toBe("94%");
    expect(confidencePercent(null)).toBeNull();
  });
});

/* ---------------------------------------------------------------------------
 * The reply, on the screen
 * ------------------------------------------------------------------------ */

describe("a gated reply an associate has to decide about", () => {
  it("is on the screen, whole, under a heading that says which way it goes", async () => {
    servePanel(replyReview());
    renderPanel();

    expect(await screen.findByRole("heading", { name: "Reply to Support" })).toBeVisible();

    // The whole body, pinned as an equality. `toContain` would pass on a
    // truncated reply, which is a message going to a supplier with its second
    // half missing.
    const field = await screen.findByLabelText("The reply");
    expect(field).toHaveValue(REPLY_TEXT);
  });

  it("says which rung answered and how sure it was", async () => {
    servePanel(replyReview());
    renderPanel();

    expect(await screen.findByText("from what this case already knows")).toBeVisible();
    expect(screen.getByText("94% confident")).toBeVisible();
    expect(screen.getByText("2 case facts cited")).toBeVisible();
    expect(screen.getByText("says it is from the platform")).toBeVisible();
  });

  it("can be approved from the keyboard alone, echoing the served hash", async () => {
    const approvals: unknown[] = [];
    servePanel(replyReview());
    fixtureServer.use(
      http.post(`/api/v1/cases/:caseId/reviews/:reviewId/approve`, async ({ request }) => {
        approvals.push(await request.json());
        return HttpResponse.json({
          data: {
            review_id: REVIEW,
            state: "APPROVING",
            draft_version: 1,
            canonical_edit_version: 0,
            signal_id: "reply_approved:review-mock-1:1",
            duplicate: false,
          },
          meta: metaBlock(),
        });
      }),
    );

    const user = userEvent.setup();
    renderPanel();
    await screen.findByLabelText("The reply");

    await user.click(screen.getByRole("button", { name: /Send to Support/ }));
    // The confirmation takes focus deliberately (V1's rule: the associate asked
    // for it), so Enter alone commits -- no pointer anywhere in this path.
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(approvals).toEqual([
        {
          draft_version: 1,
          canonical_edit_version: 0,
          // Served, never derived. A hash computed here would be a second
          // implementation of the store's compare-and-set.
          canonical_approved_payload_hash: APPROVAL_HASH,
        },
      ]);
    });
  });

  it("renders a reply containing markup as text, and pins the whole of it", async () => {
    // The inbound-to-associate direction. `messageText` is composed around an
    // answer to a question Support wrote, so Support's words reach this string.
    const hostile =
      '<img src=x onerror=alert(1)>Collected on 14 August <b>bay 3</b> — RMA-88120: confirmed.';
    servePanel(replyReview({ messageText: hostile }));
    renderPanel();

    const field = await screen.findByLabelText("The reply");

    // Both halves, as V1 phase 2's own markup test does. The equality proves the
    // characters arrived **unmangled** -- "verbatim" is a contract requirement,
    // and a neutraliser that stripped the angle brackets would pass a
    // "no <img> element" assertion while showing the associate something Support
    // did not write.
    expect(field).toHaveValue(hostile);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });

  it("warns, once the text has been rewritten, that the platform disclosure can be lost", async () => {
    servePanel(replyReview());
    const user = userEvent.setup();
    renderPanel();

    const field = await screen.findByLabelText("The reply");
    expect(screen.queryByText(/came from the platform is still there/)).toBeNull();

    await user.click(field);
    await user.keyboard(" Checked.");

    expect(await screen.findByText(/came from the platform is still there/)).toBeVisible();
  });
});

/* ---------------------------------------------------------------------------
 * Fixtures
 * ------------------------------------------------------------------------ */

function metaBlock() {
  return {
    schema_version: "1.0",
    request_id: "mock-reply",
    generated_at: new Date().toISOString(),
    freshness: "LIVE",
    partial: false,
    warnings: [],
  };
}

function baseReview(overrides: Record<string, unknown>) {
  return {
    review_id: REVIEW,
    review_kind: "TEMPLATE",
    scope_id: "support:case-mock-2026",
    request_id: "support:case-mock-2026",
    state: "OPEN",
    draft_version: 1,
    canonical_edit_version: 0,
    conflict_present: false,
    approval_hash: APPROVAL_HASH,
    draft: {},
    gaps: [],
    approved_by: null,
    approved_at_iso: null,
    recovery_status: null,
    last_delivery_error_code: null,
    hold_reason: null,
    abandon_audit: null,
    ...overrides,
  };
}

function templateReview(extraDraft: Record<string, unknown> = {}) {
  return baseReview({
    draft: { subject: "Return authorisation request", sections: [], gaps: [], ...extraDraft },
  }) as never;
}

/** Exactly the payload `reply_gating.py` writes when the gate opens a review. */
function replyReview(overrides: Record<string, unknown> = {}) {
  return baseReview({
    review_kind: "SUPPORT_REPLY",
    draft: {
      messageText: REPLY_TEXT,
      disclosesAgent: true,
      supportEventId: "evt-77",
      intent: "shipment_status",
      confidenceMillionths: 940_000,
      resolvedByRung: "case_facts",
      citedFactIds: ["fact-collected-1", "fact-bay-2"],
      consumedFactIds: ["fact-collected-1", "fact-bay-2", "fact-rma-1"],
      contextHash: "c".repeat(64),
      ...overrides,
    },
  }) as never;
}

function servePanel(review: unknown) {
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
            reviews: [review],
            return_records: [],
            support_digest: [],
            clarifications: [],
            timers: {
              template_review_deadline_iso: null,
              template_review_reminders_sent: 0,
              template_review_max_reminders: 3,
              support_deadline_iso: null,
            },
            parked_messages: 0,
            accepted_commands: [],
            sections: [],
          },
          meta: metaBlock(),
        },
        { headers: { ETag: '"reply"', "Cache-Control": "private, no-cache" } },
      ),
    ),
  );
}
