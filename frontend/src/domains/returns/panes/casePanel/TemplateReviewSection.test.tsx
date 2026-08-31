/**
 * The conflict-presence marker, which nothing was watching.
 *
 * contracts.md sect. 6 makes `conflict_present` a case-scoped, versioned marker
 * that "participates in the shared panel hash" and is "**cleared by the
 * canonical-edit write**", and the plan's item 24–25 line asks for it to be
 * "visible in shared panel state and cleared by the canonical-edit write".
 * Before this file, ACC4 removed the marker's effect on the Send control
 * (INJ-F2) and then the banner itself (INJ-F3) and the frontend suite stayed at
 * `858 passed` both times. `conflict_present: true` did not occur in a single
 * fixture in `src/`.
 *
 * **Every test here asserts its own premise**, because the failure mode this
 * audit exists to catch is a fixture in which the values under comparison
 * happen to coincide. Specifically: `blocked` in `TemplateReviewSection` is
 * `gaps.length > 0 || review.conflict_present`, so a conflicted fixture that
 * *also* carried a gap would be blocked either way and INJ-F2 would go on being
 * invisible. `expectsConflictOnly` asserts the gaps are empty before it asserts
 * anything about the block, so the block can only be the conflict's doing.
 *
 * ---
 *
 * A note for whoever came here from `TemplateReviewSection.tsx`'s own
 * docstring, which says *"`TemplateReviewSection.test.tsx` asserts that a field
 * value containing a tag renders as the literal characters"*. Until this
 * commit **this file did not exist**; that assertion lives, and has always
 * lived, in `CasePanel.test.tsx`'s *"renders a support-derived value as text,
 * never as markup"*. The escaping guarantee is real and pinned — the pointer
 * was wrong. Recorded as FE-DEFECT-4 in `.plan/acceptance/frontend-audit.md`;
 * the docstring is production source and ACC does not edit it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { resetPanelCacheForTests } from "../../../../api/casePanel";
import { resetCasePanelMocks } from "../../../../mocks/handlers/casePanelHandlers";
import { fixtureServer } from "../../../../test/server";
import { CasePanel } from "./CasePanel";
import { clearPanelSectionRenderers } from "./panelSectionRegistry";

const CASE = "case-mock-2026";
const REVIEW = "review-mock-1";

beforeEach(() => {
  resetCasePanelMocks();
  resetPanelCacheForTests();
  clearPanelSectionRenderers();
});

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  const ui: ReactNode = <CasePanel caseId={CASE} readOnly={false} />;
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/* -------------------------------------------------------------------------
 * Fixtures
 * ---------------------------------------------------------------------- */

function review(conflictPresent: boolean) {
  return {
    review_id: REVIEW,
    review_kind: "TEMPLATE",
    scope_id: "support:case-mock-2026",
    request_id: "support:case-mock-2026",
    state: "OPEN",
    draft_version: 1,
    canonical_edit_version: 0,
    conflict_present: conflictPresent,
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
      // Empty, and load-bearing. See `expectsConflictOnly`.
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

function panel(conflictPresent: boolean) {
  return {
    data: {
      case_id: CASE,
      execution: {
        status: "ok",
        reason: null,
        case_status: "AWAITING_TEMPLATE_REVIEW",
        work_item_id: null,
        awaiting: [],
        business_complete: false,
        parked_reason: null,
      },
      reviews: [review(conflictPresent)],
      return_records: [],
      timers: {
        template_review_deadline_iso: new Date(Date.now() + 40 * 60_000).toISOString(),
        template_review_reminders_sent: 1,
        template_review_max_reminders: 3,
        support_deadline_iso: null,
      },
      accepted_commands: [],
      sections: [],
    },
    meta: {
      schema_version: "1.0",
      request_id: "mock-panel",
      generated_at: new Date().toISOString(),
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  };
}

/**
 * The premise, asserted rather than assumed.
 *
 * A conflicted review that also carried a gap would be blocked by the gap, and
 * every assertion below would pass with `|| review.conflict_present` deleted.
 * That is the exact shape of the vacuity ACC3 found in the backend's delivery
 * tests, and it is cheap to make impossible here.
 */
function expectsConflictOnly(conflictPresent: boolean) {
  const only = review(conflictPresent);
  expect(only.gaps).toEqual([]);
  expect(only.draft.gaps).toEqual([]);
  expect(only.conflict_present).toBe(conflictPresent);
  expect(only.state).toBe("OPEN");
}

function servePanel(conflictPresent: boolean) {
  fixtureServer.use(
    http.get("/api/v1/cases/:caseId/panel", () =>
      HttpResponse.json(panel(conflictPresent), {
        headers: {
          ETag: conflictPresent ? '"conflicted"' : '"settled"',
          "Cache-Control": "private, no-cache",
        },
      }),
    ),
  );
}

/* -------------------------------------------------------------------------
 * Visible
 * ---------------------------------------------------------------------- */

describe("a conflict on the shared panel", () => {
  it("is on the screen, in words about the other person rather than about a flag", async () => {
    expectsConflictOnly(true);
    servePanel(true);

    renderPanel();

    expect(await screen.findByText("Somebody else is editing this draft")).toBeVisible();
    // The second sentence is the one that stops an associate hunting for text
    // that is deliberately not shown: sect. 6 keeps private edit contents out of
    // the shared payload, so there is nothing here to reveal and the copy has to
    // say so rather than leave a blank.
    expect(
      screen.getByText(/Their wording is not shown here/),
    ).toBeVisible();
  });

  it("is absent when there is no conflict, so its presence means something", async () => {
    // The other half of the pair. Without it, a banner rendered unconditionally
    // would pass the test above.
    expectsConflictOnly(false);
    servePanel(false);

    renderPanel();

    await screen.findByRole("heading", { name: "Message to Support" });
    expect(screen.queryByText("Somebody else is editing this draft")).toBeNull();
  });
});

/* -------------------------------------------------------------------------
 * Blocking
 * ---------------------------------------------------------------------- */

describe("what an unresolved conflict does to Send", () => {
  it("blocks it, and names the conflict as the reason rather than a missing field", async () => {
    expectsConflictOnly(true);
    servePanel(true);

    renderPanel();

    const send = await screen.findByRole("button", { name: /Send to Support/ });
    expect(send).toHaveAttribute("aria-disabled", "true");
    // `aria-disabled`, not `disabled`: sect. 9's keyboard path needs the control
    // to stay in the tab order and explain itself.
    expect(send).toHaveAttribute("aria-describedby", "send-blocked-reason");

    // **The wording is the assertion.** `blocked` is
    // `gaps.length > 0 || review.conflict_present` and the two limbs print
    // different sentences; the gaps are empty (asserted above), so reading the
    // conflict's sentence is what proves the conflict limb ran.
    expect(screen.getByText("Settle the other edit first.")).toBeVisible();
    expect(screen.queryByText("Fill the missing details first.")).toBeNull();
  });

  it("leaves Send pressable once nothing is in conflict", async () => {
    expectsConflictOnly(false);
    servePanel(false);

    renderPanel();

    const send = await screen.findByRole("button", { name: /Send to Support/ });
    expect(send).toHaveAttribute("aria-disabled", "false");
    expect(screen.queryByText("Settle the other edit first.")).toBeNull();
  });
});

/* -------------------------------------------------------------------------
 * Cleared by the canonical-edit write
 * ---------------------------------------------------------------------- */

describe("clearing it", () => {
  it("is done by the canonical-edit write, and the panel then unblocks", async () => {
    expectsConflictOnly(true);

    // The server clears the marker on the canonical-edit write (sect. 6), so
    // the fixture does the same and nothing else: the panel answers conflicted
    // until `POST .../edit-state/resolve` arrives, and settled afterwards. What
    // is under test is the **client** -- that the control offered beside the
    // banner issues the canonical-edit write and that the panel is re-read
    // afterwards, rather than the banner being cleared locally.
    let resolved = false;
    const resolveCalls: string[] = [];
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(panel(!resolved), {
          headers: {
            ETag: resolved ? '"settled"' : '"conflicted"',
            "Cache-Control": "private, no-cache",
          },
        }),
      ),
      http.post(
        "/api/v1/cases/:caseId/reviews/:reviewId/edit-state/resolve",
        ({ request }) => {
          resolved = true;
          resolveCalls.push(request.url);
          return HttpResponse.json({
            data: {
              review_id: REVIEW,
              state: "OPEN",
              draft_version: 1,
              canonical_edit_version: 1,
              signal_id: null,
              duplicate: false,
            },
            meta: {
              schema_version: "1.0",
              request_id: "mock-resolve",
              generated_at: new Date().toISOString(),
              freshness: "LIVE",
              partial: false,
              warnings: [],
            },
          });
        },
      ),
    );

    renderPanel();

    // Premise: we are actually starting from a blocked, conflicted panel.
    expect(await screen.findByText("Somebody else is editing this draft")).toBeVisible();
    expect(await screen.findByRole("button", { name: /Send to Support/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(resolveCalls).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Keep this version" }));

    // The canonical-edit write happened -- once, and against this review.
    await waitFor(() => {
      expect(resolveCalls).toHaveLength(1);
    });
    expect(resolveCalls[0]).toContain(`/reviews/${REVIEW}/edit-state/resolve`);

    // ...and the marker being gone is the *server's* answer arriving back
    // through the panel read, not this component hiding its own banner.
    await waitFor(() => {
      expect(screen.queryByText("Somebody else is editing this draft")).toBeNull();
    });
    expect(screen.getByRole("button", { name: /Send to Support/ })).toHaveAttribute(
      "aria-disabled",
      "false",
    );
    expect(screen.queryByText("Settle the other edit first.")).toBeNull();
  });
});
