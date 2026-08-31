# Handoff — V1 phase 2: the case review panel

Companion to `.plan/handoffs/V1-phase1.md`. Covers brief items 3, 4, 6, 7 and 8:
the workflow gate, the panel and review API, the console panel, and the
operations screen's read-only reuse of it.

Written for the three audiences that will actually open it: **V2 and V3**, who
consume the section seam and must not have to read V1's components to use it;
**ACC**, which acceptance-tests the flow; and **whoever changes this next**,
who needs the reasons and not only the values.

---

## 1. What the panel is

`GET /api/v1/cases/{case_id}/panel` composes one `CasePanelView` per read:
execution state, reviews, RMAs, timers, accepted commands and contributed
sections. It is **shared and principal-independent** — two principals who may
both read a case get byte-identical bodies and therefore identical ETags. Every
per-actor value lives at `GET .../reviews/{review_id}/edit-state`, which is
`private, no-store` and never in the panel or its hash.

Composition **always runs**; the ETag decides only whether the bytes travel
(DR-10). A 304 saves bandwidth, never work — serving a cached panel over a
review whose state had moved is the one thing an associate is watching this
screen for.

| Header | Value | Why |
| --- | --- | --- |
| `ETag` | `"<digest>"` | Quoted; an unquoted ETag is invalid and some caches drop it |
| `Cache-Control` | `private, no-cache` | Browser-cacheable, never a shared proxy, revalidated every time. `no-store` would defeat the ETag; a `max-age` would let a stale panel render over a sent review |
| `Vary` | `Authorization` | The body is principal-independent but the **404** is not |
| `Cache-Control` (edit-state) | `private, no-store` | `no-cache` still permits storage, and an autosaved draft is one person's unfinished thinking |

**Absolute instants only.** `deadline_iso` is an instant and there is no
`seconds_remaining`. A server-side countdown is stale on serialization and
changes the body every second, so no ETag would ever match — one field would
have cost every cached panel on the estate.

---

## 2. The seam V2 and V3 consume

Two registries, deliberately not one.

**Backend** — `operations/case_panel.py`:

```python
register_panel_section(section_id, contributor)   # at import time, from your own module
# contributor: async (context: Mapping[str, Any]) -> PanelSectionView | None
```

`context` carries at minimum `case_id`, `tenant_id`, `principal_id` and
`request`. Return `None` to omit the section. **Raising is caught** and becomes a
`degraded` section — a contributor must not be able to take the panel down,
because the reviews are what an associate is blocked on. Registering one id
twice raises: two contributors would race, and which won would depend on import
order. Contributors are invoked **sorted by id**, so the ETag does not move when
a module moves.

`PanelSectionView.payload` is an **opaque JSON object**. That is the seam: a
typed field per section would put V2's and V3's shapes into V1's DTO and into
every OpenAPI regeneration V1 owns. Own your payload's shape and its tests.

**Console** — `domains/returns/panes/casePanel/panelSectionRegistry.tsx`:

```ts
registerPanelSectionRenderer({ sectionId, order, render })
// render: ({ section, panel, caseId }) => ReactNode
```

`order` is explicit and ties break on `sectionId`, so the layout is total and
does not depend on import order either. A contributed section with **no**
registered renderer draws a labelled placeholder rather than vanishing —
silently dropping it would hide a server-newer-than-bundle skew from everyone
who could act on it.

V1's two built-ins are **not** registered, and the reason matters if you are
copying them: neither fits the contributor shape (status reads `CasePanelView`'s
own fields; the review section iterates `reviews[]` and dispatches mutations).
Widening the renderer contract to fit them would push V1's needs into the seam
you have to live with.

### `CasePanelView`, frozen

> ⚠ **2026-08-31: three rows below are superseded by AMENDMENT-6.**
> `support_digest[]`, `parked_messages` and `clarifications[]` are **retired
> from `CasePanelView`** — off the DTO, off the composer, out of the published
> document. Their "arrive through the section registry" note was never true of a
> *top-level field*: a contributor returns a `PanelSectionView | None` into
> `sections[]` and cannot write one. Contributed content arrives in `sections[]`
> only. The rows are preserved verbatim as the record of what was believed when
> the DTO was frozen; **do not read this table as the current field inventory
> for those three.** Current shape: `contracts.md` §9. Execution:
> `.plan/tracks/AMEND6.ledger.md`.

| Field | Owner | Notes |
| --- | --- | --- |
| `case_id`, `execution`, `timers` | V1 | `execution` degrades on a Temporal transient; timers go empty with it, because a deadline the panel invented is a countdown to nothing |
| `reviews[]` | V1 | keyed `(review_kind, scope_id)`; every non-terminal review plus the last five terminal ones |
| `return_records[]` | V1 | **narrow** projection: id, reference, status, method. No `updatedAt` — it moves on writes the panel does not render, and carrying it would invalidate every cached panel for a change nobody can see |
| `support_digest[]`, `parked_messages` | **V2** | declared and empty; arrive through the section registry |
| `clarifications[]` | **V3** | same |
| `accepted_commands[]` | V1 | **unfiltered by actor** — filtering would make the hash actor-dependent while looking like a privacy improvement |
| `sections[]` | V2/V3 | sorted by `section_id` |

### `ReviewPanelView.approval_hash` — read this before writing an approval

The console **cannot** derive `canonical_approved_payload_hash`. The store's CAS
compares against `canonical_payload_digest(canonical_review_payload(review))` —
its own canonical serialization, of the canonical edit where one exists and the
draft where it does not. A client reproducing that is a second implementation of
a compare-and-set in another language, and the two disagree the first time
either side changes how a payload serializes; every approval would then answer
409 for a reason no associate could act on.

So the panel serves it and the client **echoes** it. `None` once the review is
past `OPEN`. The guarantee is unchanged: a draft that moved between the panel
read and the approval hashes differently and is still refused.

---

## 3. Design tokens

All in `domains/returns/copilotTokens.ts` under `review`. **No hex anywhere** —
every value is an M3 role, so a theme change reaches this panel with the rest of
the console.

| Token | Roles | Usage |
| --- | --- | --- |
| `review.state.*` | six pairs over `*-container` / `on-*-container` | one badge per review state |
| `review.provenance` | `surface-container-high` / `outline`, `text-xs` | where a field's value came from |
| `review.gap` | `error` border, `error-container/30` | a required detail the case cannot answer |
| `review.conflict` | `tertiary` border, `tertiary-container/40` | another actor editing; a superseded draft; a confirmation |
| `review.field.{row,label,value,input,edited}` | `outline-control`, `primary` focus ring | the draft's fields |
| `review.action.{primary,secondary,danger,bar}` | `primary` / `outline-control` / `error` | the action bar |
| `review.liveRegion` | `outline`, `min-h-[1rem]` | reserves its own space, so an announcement causes no layout shift |

**`text-xs` (0.75rem) is the floor**, and the provenance chip is the file's own
stated minimum rather than a shade under it. Provenance is precisely what an
associate squints at when deciding whether to trust a value.

**Colour is never the only signal.** Every state badge carries its word and a
decorative (`aria-hidden`) icon, so nothing is distinguished by hue alone.

---

## 4. States, and what each one shows

| Review state | Badge word | Actions offered | Notes |
| --- | --- | --- | --- |
| `OPEN` | Awaiting your review | Send · Rebuild · Discard and start again · Cancel | the only editable state |
| `APPROVING` | Sending | none | "Approved by X. Sending to Support now." |
| `SENT` | Sent to Support | none | terminal, stays on the panel |
| `DELIVERY_FAILED` | Could not be sent | Try sending again · Stop trying to send | carries the error code |
| `HELD_FOR_OPERATIONS` | Held for operations | same | |
| `CANCELLED` / `ABANDONED` | Cancelled / Abandoned | none | abandon shows actor, reason and instant |

Also rendered: the **gap list** (a required detail the case does not know —
blocks Send and says so), the **conflict banner** (another actor holds an edit;
their wording is deliberately *not* shown), the **superseded banner** (a newer
draft arrived while editing), and per-field **provenance chips** plus an
`edited — was "…"` chip.

### Content and edge cases

| Case | Behaviour |
| --- | --- |
| No reviews, no deadline, copilot | renders **nothing**. The panel mounts under every mode; announcing an absence on every case would be permanent furniture |
| No reviews, operations view | the empty state — somebody auditing arrived deliberately and must tell "no review" from "did not load" |
| A value the platform has not produced | the word is **`Pending`**, the domain's only one. `ReturnCopilotFabrication.test.ts` enforces this and caught five invented alternatives in the first draft |
| Long field value | the input is `field-sizing-content` with `min-h-2.25rem`, so it grows rather than scrolling inside three lines and hiding the thing being checked |
| Temporal unreachable | the execution block degrades **by name** — "we could not read the workflow" and "the workflow says nothing is happening" look identical on a screen showing neither, and only one is a reason to call somebody |
| A contributed section fails | that section degrades; the reviews stay |

---

## 5. Interaction and motion

There is no motion beyond the token transitions on hover and focus. That is a
decision, not an omission: this panel is read under time pressure by somebody
holding a box, and an animated state change is a state change you have to wait
to read.

| Element | Trigger | Behaviour |
| --- | --- | --- |
| Field input | keystroke | local state immediately; autosave 800 ms after the last keystroke, coalesced under one `client_edit_id` |
| Send / Cancel / Stop trying | click or Enter | replaces the action bar with a confirmation |
| Confirmation | mount | focus moves to the confirming button |
| Confirmation | Escape or "Keep editing" | dismissed, focus returns to the button that opened it |
| Panel | 10 s poll | conditional `If-None-Match`; a 304 costs one round trip and no re-render |

### The three confirmations

Three of the five actions cannot be taken back, and each names **what will
happen** then **what it costs**, with buttons labelled by the action rather than
"OK"/"Cancel":

* **Send** — "Support will see it as it is written above. A message cannot be
  recalled once it has been sent." → *Send it* / *Keep editing*
* **Cancel this request** — "Support will not be asked, and this return will stop
  waiting for an answer. This cannot be undone." → *Cancel the request* /
  *Keep editing*
* **Stop trying to send** — "The platform will make no further attempts. Your
  name and your reason are recorded against the decision." → *Stop trying* /
  *Keep editing*

Rebuild and discard-and-start-again are **not** confirmed: they change only what
is on this screen, and confirming everything is how people learn to click
through confirmations.

The dismiss button says **"Keep editing"**, never "Cancel" — on the cancel
confirmation, a button labelled "Cancel" would be asking whether to cancel the
cancel.

---

## 6. Accessibility

Audited against WCAG 2.1 AA. Every item below is enforced by a test in
`CasePanel.test.tsx`, not merely intended.

| Criterion | How it is met |
| --- | --- |
| **2.1.1 Keyboard** | The whole path — land in a field, edit, tab to Send, Enter, confirm — with no mouse. The test tabs *forward* to Send rather than querying it, so the assertion is that Send is in the tab order |
| **2.4.3 Focus order** | Backing out of a confirmation returns focus to the button that opened it. The first attempt captured `document.activeElement` and restored on unmount, which never works: the action bar unmounts with the prompt. The restore lives in the parent, which knows which action opened it |
| **2.4.7 Focus visible** | `focus-visible:ring-2 ring-primary ring-offset-2` on every action; `focus:ring-1 focus:border-primary` on inputs |
| **3.2.1 On focus** | Nothing changes on focus. Arriving content **never** takes focus |
| **3.3.1 Errors** | Refusals are `role="alert"` and carry the transition — "this review is already approving", never "409" |
| **3.3.2 Labels** | Every field input has a `<label htmlFor>`; provenance is linked by `aria-describedby` |
| **4.1.2 Name/role/value** | `aria-disabled` on a blocked Send, **never** `disabled` — a disabled button leaves the tab order, so a keyboard associate finds nothing there and no way to learn why. Ours is reachable, announced as disabled, pressable, refuses, and names the reason via `aria-describedby` |
| **1.3.1 Structure** | One `<dl>` per section under a real `<h4>`. The first draft nested headings and groups inside one `<dl>`, which is invalid — `<dl>` may contain only `<dt>`, `<dd>` and `<div>` wrappers — and a styled `<p>` is a fake heading, invisible to the heading navigation somebody uses to skim a long draft |
| **4.1.3 Status messages** | `role="status" aria-live="polite"`, never `assertive`. An autosave confirmation is not worth interrupting somebody composing a message to a supplier |
| **Countdown** | deliberately **no** live region. One announcing itself every second would make the pane unusable with a screen reader |

### The mid-edit rule

**A support artifact arriving while somebody is typing steals no focus and drops
no edit.** The panel polls every ten seconds, so a re-render, a second actor's
conflict marker or a newly bound artifact can land between two keystrokes.

`useDraftEditor` holds the edited values keyed by `review_id` and never re-seeds
them from a poll while the associate is dirty. The newer draft is recorded, the
live region says "your edits are kept — review the new draft before sending",
and taking it is a button they press. Clean editors take the new draft silently.

The seeding is **adjusted during render**, not in an effect. An effect would
paint the old values and replace them, which for a text field is a visible flash
with a re-render landing between the halves.

---

## 7. Security

**Support-derived values are data, never markup** (dispatch condition 10). Every
value reaches the DOM as a React text child. There is no
`dangerouslySetInnerHTML` and no markdown renderer on this surface, and the test
feeds `<img src=x onerror=alert(1)>` through the panel and asserts both the
literal value **and** the absence of the element — the second alone would pass
against a field that never rendered.

On the write path, a **field** edit is neutralised through composition's own
`_safe` before storage: it replaces one value inside an agent-authored frame and
a reader cannot tell frame from value. A **whole-body** override is not
neutralised, because there is no frame left to impersonate and neutralising it
would delete the reviewer's own headings.

**A conflicting actor's wording is never shown.** The conflict marker is
case-level and participates in the panel hash; the edit contents do not.

---

## 8. What is registered for later

1. ~~A retry after the gate has closed.~~ **Settled by AMENDMENT-5** and
   implemented — see §9 below. It was worse than registered: the retry endpoint
   had no liveness guard at all, so it always succeeded and stranded the review
   in `APPROVING`, whose three exits are all workflow-driven.
2. **`editing_actors(review_id)` on S2's store.** V1's condition-8 recompute
   reads S2's draft-edit rows directly because S2 exposes no such read. The clean
   shape is one method there.
3. **The subject line.** §8's grammar is interpolation-only, so a reviewer can
   rewrite the whole body but not the subject. Carried from phase 1's item 7 and
   still open.
4. **`_stale_commands` pages platform-wide** (100) and filters to the case, so a
   busy estate can hide a case's stale command. Closing it needs a `case_id` term
   on S2's port.

---

## 9. Recovery: what AMENDMENT-5 settled, and what you must not undo

**Delivery belongs to the workflow.** §7 puts every send on one
receiver-deduped path with one `delivery_id` per approved review; a recovery
endpoint with its own send would be the second sender that design exists to
prevent. The endpoint's job is to record the command and **refuse when it cannot
be honoured**.

Two rules, and the second is not optional — rule 1 alone leaves an operator with
nothing but a refusal:

1. **`DELIVERY_FAILED → APPROVING` requires the execution to still hold the
   review.** `POST .../recovery/retry` asks `execution_state` (whose
   `template_reviews` already carries the held pairs, because the panel composes
   from it) **before** calling S2 — `retry_delivery` moves the state and records
   the command in one transaction, so a check after it would be a refusal
   reporting a change it had already made.

   | answer | status | code |
   | --- | --- | --- |
   | definitively not held | **409** | `ExecutionNoLongerHoldingReview`, carrying `state: HELD_FOR_OPERATIONS` and naming the legal action |
   | cannot reach the host | **503** | `EXECUTION_LIVENESS_UNKNOWN`, `retryable: true` |

   Both fail closed. They are two answers because "the gate has closed" and "we
   cannot tell whether it has" are different, and telling an operator the first
   when the truth is the second sends them to abandon a deliverable message.

2. **The gate parks every non-terminal review on close.** Not only `APPROVING`:
   an `OPEN` review left behind by a closed gate is the same trap through the
   **approve** endpoint. `HELD_FOR_OPERATIONS` is where they go, because §6
   already makes it the operator-decides state with `OPEN` and `ABANDONED` both
   legal from it.

   **A `continue_as_new` is not a close.** The next run re-enters the gate
   holding the same reviews, and `HELD_FOR_OPERATIONS` is a *resolved* state —
   parking there would make the resumed run find everything settled and send
   nothing, for a case nobody had answered.

**For the console (V2/V3):** a `409 ExecutionNoLongerHoldingReview` is not a
generic conflict. The correct affordances are *reopen* and *stop trying*, and
the refusal names them. A `503 EXECUTION_LIVENESS_UNKNOWN` should be offered as
"try again", never as a failure.

**S2's transition table was widened** (`OPEN → HELD_FOR_OPERATIONS`,
`DELIVERY_FAILED → HELD_FOR_OPERATIONS`) under this amendment.
`HELD_FOR_OPERATIONS → HELD_FOR_OPERATIONS` was deliberately **not** added:
re-holding would overwrite the first hold reason with a later one.
