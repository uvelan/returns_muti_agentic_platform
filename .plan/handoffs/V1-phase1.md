# Handoff spec — V1 phase 1: Support Template configuration tab

Covers what phase 1 shipped on the frontend: `Configuration → Support Template`
(`/config/support-template`). Phase 2 (case panel, review surface) is a separate
spec; where this one leaves a seam for it, it says so.

## Overview

An operator edits the support handoff template — the document that decides what
the platform says to Channel B when a return needs an RMA — and, before
publishing it, sees what it renders.

The tab answers three questions that previously had no answer short of
publishing and waiting for a real handoff:

1. Which variant does a case of *this* shape earn?
2. What text does that variant produce?
3. Where did each value come from, and which required ones are missing?

## Layout

Single column, `flex flex-col gap-4`.

| Region | Component | Notes |
| --- | --- | --- |
| Page header | `<h2>` + description | Explains selectors in one sentence. |
| Pre-template notice | `<p>` | Only when the active release carries no `support_template`. |
| Editor | `DocumentEditor` (`premium-panel`) | Header bar: kicker/subtitle/badges, mode toggle, Reset, Publish. |
| Messages | inside the editor body | read-only notice → JSON error → publish error → publish result → `notice` slot (publish steps). |
| Editor body | key-value \| split \| JSON | Split is a 1-col / `xl:`2-col grid. |
| Preview | `VariantPreview` | Rendered through the editor's `footer(document)` slot so it always sees the *current* draft. |

Responsive: the editor's mode toggle and buttons sit in a flex header that wraps;
the preview's context controls are `grid-cols-1 sm:grid-cols-2 xl:grid-cols-4`;
the rendered message is a `<pre>` with `overflow-auto` and `max-h-96` so a long
handoff scrolls inside its own box rather than stretching the page.

## Design tokens

No new token, no hex, no arbitrary colour. Everything is an existing M3 role or
an existing utility class from the console's own layer.

| Token / class | Usage |
| --- | --- |
| `premium-panel` | Editor shell |
| `premium-kicker` | Section eyebrows ("Preview", "Subject", "Message") |
| `premium-field` | Preview context inputs |
| `surface-container-lowest` / `-low` / `-container` | Panel, row, and chip grounds |
| `outline-variant` / `outline-control` / `outline` | Borders and de-emphasised text |
| `on-surface` / `on-surface-variant` | Body and secondary text |
| `primary` / `on-primary` | Publish button, active mode |
| `secondary-container` / `on-secondary-container` | Release badge, record chip, publish result |
| `tertiary-container` / `on-tertiary-container` | "Unsaved changes", "Fallback used" |
| `error-container` / `on-error-container` / `error` | JSON refusal, publish refusal, gap list |
| `rail-surface` / `rail-on-surface` | Monospace surfaces (JSON editor, rendered message) |

## Components

| Component | File | Notes |
| --- | --- | --- |
| `DocumentEditor<TResult>` | `domains/config/DocumentEditor.tsx` | Lifted from `AgentsSection`, behaviour unchanged. Generic over the write path's result. |
| `SupportTemplateSection` | `domains/config/SupportTemplateSection.tsx` | The tab. |
| `VariantPreview` / `RenderedPreview` / `FieldProvenance` | same file | Preview controls and output. |
| `PublishProgress` | `components/PublishProgress.tsx` | Moved out of the AI Control Center; shared. |
| `runPublishPipeline` / `defaultReleaseId` | `api/releasePublish.ts` | Moved likewise. |
| `supportTemplateApi.preview` | `api/supportTemplate.ts` | Response typed from the generated schemas. |

### `DocumentEditor` props that matter to a future caller

`kicker`, `subtitle`, `badges`, `loaded`, `canWrite`, `jsonLabel` (the
textarea's accessible name — a screen with two editors needs two),
`submitLabel` / `submittingLabel` / `submitTitle`, `readOnlyNotice`,
`notObjectMessage`, `confirmSubmit?`, `onSubmit`, `renderResult?`, `notice?`,
`onDirtyChange`, `footer?(document | null)`.

`confirmSubmit` is present only where the button is the irreversible half.
Agents propose; this one publishes.

## Write path

`support_template` is a field on `ReturnPlatformConfiguration`, so it travels
the platform's one release lifecycle:

```
POST   /api/config/releases                              (DRAFT, cloned from active)
PATCH  /api/config/releases/{id}/domains/RETURN_PLATFORM {patch: {support_template: …}}
POST   /api/config/releases/{id}/promote                 VALIDATED
POST   /api/config/releases/{id}/promote                 RELEASED, expected_head_revision
```

The PATCH validates the *result* against the full model, so a template the
backend would refuse is a 422 with the model's own words and nothing is stored.
The head revision comes from the runtime snapshot and is the optimistic guard:
two operators publishing different releases cannot both win.

Capability: `config.release.promote`. Without it the editor is read-only and
says so — **preview still works**, which is the point of separating them.

## Preview

`POST /api/v1/config/support-template/preview` — body `{template, context}`,
capability `RETURNS_SESSION_READ`, response inside the platform envelope.

- The template sent is the draft in the editor, not the published one.
- The case is fabricated server-side; no case id is accepted, so a preview can
  never read customer data.
- No graph port, so a preview spends no on-demand sync: a `graph:` binding
  previews as its fallback or as a gap, and the UI labels which.
- Context clauses are free text, comma separated, trimmed, empties dropped.

## States

| Element | State | Behaviour |
| --- | --- | --- |
| Tab | Loading | "Loading..." while the runtime snapshot is in flight. |
| Tab | Snapshot error | `role="alert"`, the server's message verbatim. |
| Tab | No template on the release | Notice: handoffs are still composed the built-in way; editor seeded with an empty `variants` list, which is the honest pre-template state. |
| Editor | Dirty | "Unsaved changes" chip; Reset and Publish enabled; navigation guarded (`beforeunload`, link clicks, history). |
| Editor | Invalid JSON | Mode switch refused with the parser's message rather than silently dropping the edit. |
| Editor | Read-only | All fields disabled, notice explains the missing capability. |
| Publish | Confirm | "Publish this template as a new configuration release? Cases opened afterwards pin it." Declining calls nothing. |
| Publish | Running | Button reads "Publishing..."; the four steps stream under the header, the failing one marked. |
| Publish | Success | "Release … is published. Cases opened from now on pin it; cases already running keep the template they started with." |
| Publish | Refused | The backend's message verbatim, `role="alert"`. |
| Preview | Idle | "Nothing rendered yet. Set the case shape above, then render." |
| Preview | Draft unparseable | Button disabled with a `title`; the live region explains and promises the preview returns. |
| Preview | Rendered, no gaps | "Variant X rendered with every required field filled." |
| Preview | Rendered with gaps | Same line with the count, plus an error-container block: "This draft would be held rather than sent," then `field_id — reason` per gap. |
| Field | Fallback applied | "Fallback used" chip — words, not colour alone. |
| Section | Per-record | "Record {return_record_id}" chip; multi-record requests render one group per record. |

## Accessibility

Audited against WCAG 2.1 AA; the assertions live in
`SupportTemplateSection.a11y.test.tsx` (6) and mirror
`AgentsSection.a11y.test.tsx`.

- **3.3.2 / 4.1.2 Labels, name-role-value.** Every generated field takes its
  programmatic name from the parent object's key via `aria-labelledby` — the
  editor's original defect was that the name was visible but not programmatic.
  The JSON textarea has a subject-specific `aria-label`. All four preview
  controls use `<label for>`. Asserted as a relationship, so a bigger template
  cannot reintroduce the gap.
- **4.1.3 Status messages.** One `role="status"` region, present from first
  render rather than created with its content, carrying the one-line summary
  only — putting the whole rendered handoff in a live region would read the
  entire message aloud on every render.
- **2.1.1 / 2.4.3 Keyboard.** Every control is a native `<button>`, `<input>`,
  `<select>` or `<textarea>`; the path edit → render → read is asserted on the
  keyboard, and the result arriving leaves focus where it was.
- **1.4.1 Use of colour.** Gaps, fallbacks and failed publish steps all carry
  text; colour is never the only signal.
- **Contrast** rides the existing M3 role pairs (`on-*` on their own
  container), which the console's contrast tests already cover.

Known limitation, recorded rather than hidden: the publish confirmation is
`window.confirm`, so its buttons read "OK"/"Cancel" rather than naming the
action. That is the console's existing idiom for destructive confirmations
(discard, reset, switch agent). A shared dialog component with action-labelled
buttons would fix all of them at once and is worth doing as one change, not as
a one-off here.

## Edge cases

- Multi-record renders: one section group per `return_record_id`, keyed on
  `section_id:return_record_id` so two groups of the same section coexist.
- A section with no fields renders "No fields." rather than an empty box.
- Long values wrap (`break-words`); the message body scrolls in its own box.
- `item_count` coerces a half-typed or empty entry to `0` rather than `NaN`,
  the same rule the editor's number leaves use.
- Switching releases remounts the editor (`key={releaseId}`), so a stale draft
  cannot survive onto a different release.

## Seams left for phase 2

- The editor's `footer` and `notice` slots are how another screen adds its own
  panel without forking the editor.
- `PublishProgress` and `runPublishPipeline` are shared and take a domain key,
  so a future behaviour-domain editor needs no fourth copy.
- Nothing here touches the review aggregate, the workflow gate, or the case
  panel; the preview endpoint is render-only and holds no state.
