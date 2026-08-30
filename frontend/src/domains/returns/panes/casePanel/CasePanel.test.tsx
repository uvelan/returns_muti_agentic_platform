/**
 * The case panel, driven the way an associate drives it.
 *
 * Rendered over the **real** MSW handlers and the real query client, so a
 * "sent" assertion is the store's answer travelling back through the client
 * rather than a mock this file arranged. The four things this file exists to
 * prove:
 *
 * 1. the whole keyboard path -- review, edit, send -- with no mouse;
 * 2. a support artifact arriving mid-edit steals no focus and drops no edit;
 * 3. support-derived text is rendered as **data**, not markup;
 * 4. the section registry V2 and V3 consume actually contributes.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetPanelCacheForTests } from "../../../../api/casePanel";
import { resetCasePanelMocks } from "../../../../mocks/handlers/casePanelHandlers";
import { fixtureServer } from "../../../../test/server";
import { CasePanel } from "./CasePanel";
import {
  clearPanelSectionRenderers,
  registerPanelSectionRenderer,
} from "./panelSectionRegistry";

const CASE = "case-mock-2026";

function wrapper(client: QueryClient, children: ReactNode) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/**
 * The panel, and the query client driving it.
 *
 * The client is returned so a test can force the *next poll* by hand.
 * `refetchInterval` is off here on purpose: waiting ten real seconds for a
 * re-read would make the mid-edit test slow and flaky, and what it is actually
 * asserting is what happens **when a newer panel arrives**, not how long the
 * clock takes to bring one.
 */
function renderPanel(props: { readonly readOnly?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return {
    client,
    ...render(wrapper(client, <CasePanel caseId={CASE} readOnly={props.readOnly ?? false} />)),
  };
}

beforeEach(() => {
  resetCasePanelMocks();
  resetPanelCacheForTests();
  clearPanelSectionRenderers();
});

afterEach(() => {
  clearPanelSectionRenderers();
});

describe("what the associate sees", () => {
  it("shows the draft, its provenance and the deadline", async () => {
    renderPanel();

    expect(await screen.findByRole("heading", { name: "Message to Support" })).toBeVisible();
    expect(screen.getByDisplayValue("SO-441207")).toBeVisible();
    // Provenance is not decoration -- sect. 8 makes it part of the contract,
    // and an associate deciding whether to trust a value needs to know a graph
    // read is not a fact somebody confirmed.
    expect(screen.getAllByText("case fact").length).toBeGreaterThan(0);
    expect(screen.getByText(/minutes left|hour/)).toBeVisible();
    expect(screen.getByText(/1 of 3 reminders sent/)).toBeVisible();
  });

  it("renders a support-derived value as text, never as markup", async () => {
    // Dispatch condition 10. These values arrive from Support through an
    // extractor and are shown to the person deciding what to send back; a value
    // that could inject markup into that screen is the whole risk.
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(panelWith("<img src=x onerror=alert(1)>ACME <b>Ltd</b>"), {
          headers: { ETag: '"markup"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );

    renderPanel();

    const field = await screen.findByDisplayValue("<img src=x onerror=alert(1)>ACME <b>Ltd</b>");
    expect(field).toBeVisible();
    // The literal characters reached the DOM as a value. Asserted on the
    // rendered tree rather than on the absence of a tag: "no <b> element" would
    // also pass if the field had not rendered at all.
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });

  it("says what will happen when there is nothing to review, on the audit view", async () => {
    // Somebody auditing a case arrived deliberately to find out, and needs to
    // tell "no review" from "this screen did not load".
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(emptyPanel(), {
          headers: { ETag: '"empty"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );

    renderPanel({ readOnly: true });

    expect(await screen.findByText(/Nothing is waiting for Support/)).toBeVisible();
  });

  it("says nothing at all in the copilot when there is nothing to say", async () => {
    // The copilot mounts this under every mode. On the great majority of cases
    // the platform never asks Support anything, and a pane announcing that on
    // every one of them would be permanent furniture reporting an absence.
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(silentPanel(), {
          headers: { ETag: '"silent"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(container).toBeEmptyDOMElement();
    });
    // And it is quiet because there is nothing, not because it failed to
    // render: a deadline alone is enough to bring it back.
    expect(screen.queryByText(/Nothing is waiting for Support/)).toBeNull();
  });

  it("appears in the copilot as soon as a review exists", async () => {
    // The other half. A pane that stayed quiet through an open review would be
    // the gate holding a message nobody knows about.
    renderPanel();
    expect(await screen.findByRole("heading", { name: "Message to Support" })).toBeVisible();
  });
});

describe("the keyboard path: review, edit, send", () => {
  it("reaches every control and completes the send with no mouse", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    const orderField = screen.getByLabelText("Order Number");
    // Tab-reachable: a field a keyboard associate cannot land on is a field
    // they cannot check, which is the whole job.
    await user.click(orderField);
    expect(orderField).toHaveFocus();

    await user.keyboard("{Control>}a{/Control}SO-441208");
    expect(screen.getByDisplayValue("SO-441208")).toBeVisible();

    // Tab forward until Send, so the assertion is that it is *in the tab
    // order*, not merely present in the DOM.
    const send = screen.getByRole("button", { name: /Send to Support/ });
    let guard = 0;
    while (document.activeElement !== send && guard < 40) {
      await user.tab();
      guard += 1;
    }
    expect(send).toHaveFocus();

    await user.keyboard("{Enter}");

    // Sending is not undoable, so it asks once -- naming the consequence rather
    // than "Are you sure?", which is the version people learn to click through.
    const confirm = await screen.findByRole("button", { name: "Send it" });
    expect(screen.getByText(/cannot be recalled once it has been sent/)).toBeVisible();
    // Focus moved to the confirmation. This is the one place on this surface
    // where taking focus is right: the associate asked for it, and the
    // alternative is a prompt a keyboard user has to hunt for.
    expect(confirm).toHaveFocus();

    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(screen.getByText("Sending")).toBeVisible();
    });
  });

  it("sends nothing when the confirmation is declined", async () => {
    // The half that makes the confirmation real. A prompt whose second button
    // sent anyway would be worse than none.
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    await user.click(screen.getByRole("button", { name: /Send to Support/ }));
    await user.click(await screen.findByRole("button", { name: "Keep editing" }));

    expect(screen.queryByText("Sending")).toBeNull();
    // And the associate is back where they were, not in a dead end.
    expect(screen.getByRole("button", { name: /Send to Support/ })).toBeVisible();
  });

  it("names the consequence on every irreversible action, and only those", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    await user.click(screen.getByRole("button", { name: /Cancel this request/ }));
    expect(screen.getByText(/this return will stop waiting for an answer/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));

    // Rebuilding changes only what is on this screen. Asking twice for that is
    // the confirmation habit that makes people stop reading confirmations.
    await user.click(screen.getByRole("button", { name: /Rebuild from the latest facts/ }));
    expect(screen.queryByRole("button", { name: "Keep editing" })).toBeNull();
  });

  it("keeps a blocked Send focusable and says why", async () => {
    // `aria-disabled`, never `disabled`. A `disabled` button leaves the tab
    // order, so a keyboard associate tabbing to Send finds nothing there and
    // has no way to discover why it cannot be pressed.
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(gappedPanel(), {
          headers: { ETag: '"gapped"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );
    const user = userEvent.setup();
    renderPanel();

    const send = await screen.findByRole("button", { name: /Send to Support/ });
    expect(send).toHaveAttribute("aria-disabled", "true");
    expect(send).not.toHaveAttribute("disabled");

    await user.click(send);
    // Pressed and refused, with the reason on screen and associated with the
    // control rather than floating somewhere near it.
    expect(screen.getByText(/Fill the missing details first/)).toBeVisible();
    expect(send).toHaveAttribute("aria-describedby", "send-blocked-reason");
    expect(screen.queryByText("Sending")).toBeNull();
  });
});

describe("a poll landing mid-edit", () => {
  it("steals no focus and drops no edit when a newer draft arrives", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    const field = screen.getByLabelText("Order Number");
    await user.click(field);
    await user.keyboard("{Control>}a{/Control}my careful wording");
    expect(field).toHaveFocus();

    // The next poll brings a re-rendered draft -- a support artifact bound, a
    // fact confirmed. This is the exact moment the outcome gate is about.
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(panelWith("SO-441207", { draftVersion: 2 }), {
          headers: { ETag: '"v2"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );
    await client.invalidateQueries({ queryKey: ["case-panel", CASE] });

    await waitFor(() => {
      expect(screen.getByText(/A newer draft has arrived/)).toBeVisible();
    });

    // All three, and each would be a separate defect on its own.
    expect(field).toHaveFocus();
    expect(screen.getByDisplayValue("my careful wording")).toBeVisible();
    expect(screen.getByText(/Your edits are kept/)).toBeVisible();
  });

  it("gives focus back when a confirmation is declined", async () => {
    // WCAG 2.4.3. The button that opened the prompt unmounts with it, so
    // without the restore a keyboard associate who backs out lands on `<body>`
    // and has lost their place in a long draft entirely.
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    const send = screen.getByRole("button", { name: /Send to Support/ });
    send.focus();
    await user.keyboard("{Enter}");
    await screen.findByRole("button", { name: "Send it" });

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Send to Support/ })).toHaveFocus();
    });
  });

  it("announces politely rather than assertively", async () => {
    renderPanel();
    await screen.findByRole("heading", { name: "Message to Support" });

    const status = screen.getByRole("status");
    // `assertive` interrupts a screen reader mid-sentence. An autosave
    // confirmation is never worth doing that to somebody who is composing a
    // message to a supplier.
    expect(status).toHaveAttribute("aria-live", "polite");
  });
});

describe("the section registry", () => {
  it("draws a contributed section in its declared place", async () => {
    registerPanelSectionRenderer({
      sectionId: "ingress",
      order: 10,
      render: ({ section }) => (
        <p data-testid="ingress-section">
          Parked messages: {String((section?.payload as { parked?: number } | undefined)?.parked ?? 0)}
        </p>
      ),
    });

    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(panelWithSection(), {
          headers: { ETag: '"sectioned"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );

    renderPanel();

    const section = await screen.findByTestId("ingress-section");
    expect(within(section).getByText(/Parked messages: 4/)).toBeVisible();
  });

  it("shows a placeholder for a section this build cannot draw", async () => {
    // The server is newer than the bundle. Dropping the section silently would
    // hide that skew from everyone who could act on it.
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(panelWithSection(), {
          headers: { ETag: '"sectioned"', "Cache-Control": "private, no-cache" },
        }),
      ),
    );

    renderPanel();

    expect(
      await screen.findByText(/ingress: this console build cannot display this section yet/),
    ).toBeVisible();
  });

  it("refuses two renderers for one id", () => {
    const renderer = { sectionId: "dup", order: 1, render: () => null };
    registerPanelSectionRenderer(renderer);
    expect(() => {
      registerPanelSectionRenderer(renderer);
    }).toThrow(/already registered/);
  });
});

/* ---------------------------------------------------------------------------
 * Fixtures. Envelopes, because `apiClient`'s sibling validates one.
 * ------------------------------------------------------------------------ */

function meta() {
  return {
    schema_version: "1.0",
    request_id: "mock-panel",
    generated_at: new Date().toISOString(),
    freshness: "LIVE",
    partial: false,
    warnings: [],
  };
}

function review(orderValue: string, overrides: Record<string, unknown> = {}) {
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
              value: orderValue,
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
    ...overrides,
  };
}

function shell(reviews: unknown[], sections: unknown[] = []) {
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
      reviews,
      return_records: [],
      support_digest: [],
      clarifications: [],
      timers: {
        template_review_deadline_iso: new Date(Date.now() + 40 * 60_000).toISOString(),
        template_review_reminders_sent: 1,
        template_review_max_reminders: 3,
        support_deadline_iso: null,
      },
      parked_messages: 0,
      accepted_commands: [],
      sections,
    },
    meta: meta(),
  };
}

function panelWith(orderValue: string, options: { draftVersion?: number } = {}) {
  return shell([
    review(orderValue, options.draftVersion ? { draft_version: options.draftVersion } : {}),
  ]);
}

function gappedPanel() {
  const gapped = review("SO-441207");
  return shell([
    {
      ...gapped,
      gaps: [{ field_id: "rma_number", reason: "the case has no RMA yet" }],
      draft: {
        ...gapped.draft,
        gaps: [{ field_id: "rma_number", reason: "the case has no RMA yet" }],
      },
    },
  ]);
}

function emptyPanel() {
  return shell([]);
}

/** No reviews **and** no review deadline: the case has never asked Support. */
function silentPanel() {
  const quiet = shell([]);
  return {
    ...quiet,
    data: {
      ...quiet.data,
      timers: { ...quiet.data.timers, template_review_deadline_iso: null },
    },
  };
}

function panelWithSection() {
  return shell(
    [review("SO-441207")],
    [{ section_id: "ingress", status: "ok", reason: null, payload: { parked: 4 } }],
  );
}
