# Google Stitch prompts — Returns Platform UI

Paste **Block 0** first to establish the design system, then one screen block per
generation. Stitch produces better results from one focused screen at a time than from a
whole app in a single prompt; the shared block keeps them visually consistent.

Every field, state and action below is taken from the live API contracts, not invented.
Where an endpoint does not exist, the screen is told to show an explicit unavailable state
rather than a fake control — that is a real requirement of this product, not a placeholder.

---

## Block 0 — Design system (paste once, before any screen)

> I am designing an internal operations web console for a retail **returns platform**. It
> is used by warehouse associates, support agents, logistics coordinators, configuration
> administrators and AI operators — not by customers. Screens are dense, information-first,
> and used for a full shift.
>
> **Visual language**
> - Desktop-first, 1440×900 primary. Must remain usable at 1280 wide.
> - Neutral slate palette: near-white page background, white cards, slate-900 for primary
>   text and the active navigation state, slate-500 for secondary text.
> - Semantic colours used sparingly and only for state: emerald for healthy/passed/active,
>   amber for warning/degraded/pending, red for failed/error, slate for inactive/superseded.
> - Cards: white, 1px slate-200 border, 8px radius, no drop shadows.
> - Typography: one sans-serif family. 24px page titles, 14px body, 12px uppercase
>   letter-spaced section labels, 12px monospace for identifiers, hashes, and trace IDs.
> - 4px spacing grid. Generous vertical rhythm inside cards, tight inside tables.
> - Tables: 12px uppercase column headers, 14px rows, 1px row separators, hover highlight,
>   entire row clickable where a detail panel exists.
> - No decorative illustration, no marketing tone, no gradients, no emoji.
>
> **Universal states — design all four for every screen**
> 1. **Loading** — quiet text or skeleton rows, never a full-page spinner.
> 2. **Empty** — states plainly that there is no data, and why if known.
> 3. **Error** — the server's message in red, inline in the panel that failed, never a
>    modal, and never blocking the rest of the screen.
> 4. **Unauthorised** — a short sentence naming the missing permission. This is a normal
>    state, not an error style.
>
> **Permission model.** The UI hides or disables what the signed-in user cannot do, but the
> server is authoritative and re-checks everything. Never design a control that implies the
> UI grants access. Disabled controls should look deliberately disabled, not broken.
>
> **A hard rule specific to this product:** several capabilities are genuinely not built on
> the backend yet. Where noted, the design must show an honest, calm "not available" panel
> explaining what is missing — not a mock control, and not a fake chart. These are permanent
> design elements until those APIs ship.

---

## Block 1 — Application shell and navigation

> Design the persistent application shell.
>
> **Left sidebar, 256px fixed, white, right border:**
> - Top block: product name "Returns Platform", and beneath it the signed-in user's
>   identifier in small muted text (e.g. `dev-operator`), truncated with ellipsis.
> - Navigation list of exactly four destinations, each an icon plus label:
>   - Return Business Copilot
>   - Configuration
>   - Graph Schema Analyzer
>   - AI Control Center
> - The active item is a filled slate-900 rounded rectangle with white text. Inactive items
>   are slate-700 with a light hover fill.
> - No sub-navigation, no version switcher, no collapsed state, no search.
> - Any destination the user lacks permission for is **removed entirely**, not greyed out.
>
> **Main region:** page content with 24px padding, scrolls independently of the sidebar.
>
> **Also design these three full-page states:**
> - **Signing in / loading permissions** — centred, one line of text.
> - **Not signed in** — centred heading "Sign in required" with one explanatory sentence.
> - **No domains available** — the user is authenticated but has been granted nothing;
>   heading plus a sentence telling them to ask an administrator to review their roles.
>   The sidebar navigation is empty in this state.

---

## Block 2 — Return Business Copilot

> Design the main returns operations screen. One screen serves support, warehouse and
> logistics roles — do **not** design separate applications for them.
>
> **Page header:** title "Return Business Copilot", subtitle "Discovery through resolution,
> one operational screen."
>
> **Three-column layout: 256px | flexible | 320px.**
>
> **Left column — Queues.** Four buttons, each with a label and a live count badge:
> My Returns, Support, Warehouse, Closed. Below a divider, the list of returns in the
> selected queue. Each list row shows the order reference in medium weight and, beneath it
> in small muted text, the status and current stage. The selected row is highlighted.
>
> **Centre column — Workspace.** Stacked cards:
> - **Stage progress**: a horizontal stepper across the return lifecycle —
>   Discovery → Analysis → Decision → RMA → Fulfillment → Warehouse → Resolution — with
>   completed, current and upcoming steps visually distinct, plus a percentage.
> - **Timeline**: a vertical event list with a left rule. Each entry shows the event type in
>   medium weight, and beneath it the actor type, actor id and timestamp in small muted
>   text. Design a variant that visually distinguishes an event produced by an **agent**
>   from one produced by a **person** — this distinction matters to operators.
> - **Record an event** (the only way to move a return forward): a form with a dropdown of
>   event types, a required evidence-reference text field, an optional JSON payload area,
>   and a primary submit button. Design the error state where the server rejects the
>   transition as not currently legal, showing the reason inline beneath the form.
> - **Conversation**: a message thread between the user and the discovery agent. Also design
>   the variant where a return did not originate from a conversation and the panel simply
>   says so — this is common and must not look broken.
>
> **Right column — Return context.** A stack of labelled groups, each a small heading over
> label/value rows with the value right-aligned and truncated:
> - Customer and order: customer reference, order reference, order source, channel
> - Items: quantity, package count, item references, reason code
> - Decision and RMA: status, return reference, approved return method, support ticket
> - Fulfillment and warehouse: tracking reference, physical return status, warehouse status,
>   bay reference
> - Resolution: customer resolution status, vendor recovery status, case closure status
> - Failure (only rendered when a failure exists): failure code and message, in red
>
> **Also design a detail drawer** that slides from the right at about 60% width, with tabs:
> Timeline, Artifacts, Evidence, Support, Audit. The Evidence tab shows grouped collections
> — return items, handling units, pickup, shipping instructions, shipment events,
> integration commands, vendor return links, agent decisions.
>
> **Empty state:** "This queue is empty." Design the whole screen with no return selected —
> the centre and right columns each show a short prompt to select a return.

---

## Block 3 — Configuration

> Design the platform configuration screen. Horizontal tab bar under the page header, with
> an underline indicating the active tab.
>
> **Tabs:** Overview, Runtime, Releases, Data Sources, Integrations, Business, Modules,
> Security, Audit.
>
> **Overview** — three summary cards in a row: runtime snapshot health (loaded and serving,
> or an error), the currently released configuration identifier in monospace, and a count of
> releases.
>
> **Runtime** — a card containing a large, scrollable, read-only JSON viewer on a tinted
> background with monospace text, capped in height. Include a small note beneath stating the
> preview is truncated when it exceeds a size limit.
>
> **Releases** — a table (release identifier in monospace, status pill, approved-by,
> activated timestamp) beside a 384px detail panel. The detail panel shows checksum in
> monospace, and the lifecycle timestamps — created, validated, approved, approved by,
> activated, superseded by — where an un-reached lifecycle step renders as a dash.
> Beneath, a **Promote** action area: buttons for the legal next transitions only, with
> illegal transitions absent rather than disabled. Design a confirmation step for promotion,
> since it changes what the platform runs.
> Status pills: released/active in emerald, superseded or rejected in muted slate,
> everything in progress in amber.
>
> **Data Sources** — a list of configured sources with health indicators. Secret values must
> be shown as `vault://...` reference pointers in monospace, never as resolved values.
> Include a visible note that secrets are redacted server-side.
>
> **Audit** — a records table with action, target, actor and timestamp.
>
> **Integrations, Business, Modules, Security** — these have no dedicated endpoint, and the
> design must say why rather than showing an empty shell. Each renders a short muted
> paragraph: Integrations and Business are already visible within the Runtime snapshot;
> Modules would always be empty by design; Security is not configuration because the role
> model lives in code. Style these as calm informational panels, not errors.

---

## Block 4 — Graph Schema Analyzer

> Design a screen for analysing data sources and proposing a graph schema.
>
> **Page header:** title "Graph Schema Analyzer", subtitle "Source-driven schema proposal,
> validation, and approval."
>
> **Three-column layout: 288px | flexible | 352px.**
>
> **Left column — Sources and analyses.** A list of analysis sessions, each showing its
> identifier in monospace and its status beneath. A "New analysis" action allows selecting
> source datasets. Include a permanent, visible note that sources are **read-only** and that
> the tool never offers to modify a source's own schema — this is a safety property of the
> product.
>
> **Centre column — Graph canvas.** An interactive node-and-edge diagram of the proposed
> schema: entity nodes as rounded rectangles with the entity name and property count,
> relationships as directed labelled edges. Include zoom and fit-to-view controls, and a
> selected-node state that highlights the node and its immediate relationships. Above the
> canvas, show summary counts of entities and relationships.
>
> **Right column — Analyzer Copilot.** A conversational panel:
> - Open clarification questions from the analyzer, each with a multi-line answer field and
>   a submit button.
> - A history of previously answered clarifications, condensed.
> - A disabled variant of the whole panel for users with read-only access, with a single
>   line explaining that answering requires edit permission.
>
> **Bottom tab strip** spanning the full width beneath the three columns, with tabs:
> Properties, Mapping, Indexes, Validation, Sync, Versions.
> - **Validation**: a Validate button, a pass/fail banner, and a findings list where each
>   finding shows a severity chip, the element name, and the message. An Approve button that
>   is enabled only after a passing validation.
> - **Versions**: a revision table — sequence, author, whether it was authored by a model,
>   mutation count, created timestamp — and a diff view showing added, changed and removed
>   schema elements with colour coding.
> - **Properties, Mapping, Indexes, Sync**: design these as honest unavailable panels, each
>   with one muted sentence stating that this data is not exposed by the API.

---

## Block 5 — AI Control Center

> Design an observability and intervention console for the platform's AI usage.
>
> **Page header:** title "AI Control Center", subtitle "Requests, interceptions, metrics,
> routes, and safety." Horizontal tab bar: Overview, Requests, Interceptions, Metrics,
> Providers & Models, Routes & Tasks, Safety, Configuration, Audit.
>
> **Overview / Metrics** — a row of six stat tiles: attempts, success rate as a percentage,
> failures, fallbacks, blocked by safety, total tokens. Beneath, four breakdown cards — by
> provider, by model, by task, by tier — each a sorted label/count list with a subtle
> proportional bar. A small estimated-cost line in muted text.
>
> **Requests** — a table (task, provider, model, status, latency, tokens) beside a 352px
> inspection panel. A row whose request used a fallback shows a small amber "fallback" chip
> next to its status. The inspection panel shows: trace identifier, attempt number,
> selection reason, configured tier versus selected tier, route, safety status, rate-limit
> wait, input and output tokens, error code, and request and response **digests** in
> monospace.
> **Critical:** this panel must never display model reasoning text or prompt bodies. Include
> a short muted note stating that digests are shown rather than payloads. Do not design any
> "view full prompt" affordance.
>
> **Interceptions** — a queue of AI requests held for a human to answer. Four count tiles:
> Pending, Claimed, Responded, Expired. A table with interception identifier, status, task,
> claimed-by, and response origin. Selecting a row opens a **manual response editor**:
> - The held request context, rendered read-only.
> - A structured response editor showing the expected response schema, with inline
>   validation that prevents structurally invalid input before submission.
> - Primary "Submit response" and secondary "Cancel interception" actions, both with
>   confirmation.
> - **Response origin must always be displayed verbatim** so a human-authored answer is
>   never mistaken for a model's. Design a distinct visual treatment for human-origin
>   responses.
> - Design the unavailable variants for Claim, Generate Candidate, Replay Same Route,
>   Replay Alternate Route and Release: these have no backing API and must appear as a muted
>   note listing them as not yet available, not as disabled buttons.
>
> **Routes & Tasks / Providers & Models** — two stacked tables. Routes: route identifier,
> provider, model, tier, circuit-breaker state as a coloured pill (closed = emerald,
> open = red, half-open = amber), active requests, requests per minute, and an
> "unconfigured" marker where applicable. Tasks: task identifier, tier, prompt version,
> fallback strategy, whether tier escalation is allowed, and allowed providers.
>
> **Safety, Configuration, Audit** — unavailable panels with a one-sentence explanation
> each, matching the honest-absence pattern used elsewhere.

---

## Block 6 — Shared components (optional, for a component sheet)

> Design a component sheet for this console containing: a status pill in emerald / amber /
> red / slate variants; a stat tile; a data table with header, hover and selected rows; a
> label/value detail row; a section group heading; an inline error message; an empty state;
> an unavailable-feature note; a read-only JSON viewer; a confirmation dialog; a slide-over
> detail drawer; a horizontal tab bar; a left navigation item in active and inactive states;
> and a monospace identifier chip with a copy affordance.

---

## Notes for whoever runs these

- **Generate one block per Stitch session**, keeping Block 0 in context. Whole-app prompts
  lose the per-screen detail.
- The **honest-absence panels are deliberate product requirements**, not gaps to design
  around. If Stitch invents controls for them, re-prompt with that constraint restated —
  a mock control that cannot work is worse than a stated absence.
- Two constraints are **non-negotiable and safety-related**: model reasoning text is never
  displayed, and a human-authored AI response is never presented as a model's.
- Screen inventory: 4 domain screens + shell + 3 shell-level states. Route map:
  `/returns`, `/config`, `/graph-schema`, `/ai`.
