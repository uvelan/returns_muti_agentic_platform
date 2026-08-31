# V2 phase 2 — the support panel sections and the transcript system entry

Handoff spec. Written for whoever builds the **backend contributor** that fills
these sections, for the **V3 sibling** contributing through the same registry,
and for the reviewer.

Branch `feat/v2-frontend`. Everything below is implemented and tested unless a
row says otherwise.

---

## 1. What this adds

Four sections into V1's console section registry, and one new entry kind in the
Order Discovery transcript.

| Section id | Order | What it shows |
| --- | --- | --- |
| `support_announcements` | 0 | nothing visible — one polite live region |
| `support_parked_messages` | 10 | a count, why, and that nothing is lost |
| `support_return_records` | 20 | the artifact cards, the bay, the unfiled artifacts |
| `support_thread_digest` | 30 | the messages Support sent |

Reading order, not an arbitrary one: parked first because it is the section that
says the panel below it is **not** the whole story; the records next because that
is what an associate came for; the digest last because it is the evidence behind
them.

Files, all V2's own:

```
frontend/src/domains/returns/panes/casePanel/support/
  supportPanelPayloads.ts        the readers and the shapes
  supportSections.tsx            the four renderers
  installSupportSections.tsx     registration + the one import others make
  supportCopy.ts                 associate-facing wording
  supportSystemEntries.ts        the DR-3 entry reader and its sentence
frontend/src/mocks/handlers/supportHandlers.ts
```

Touched elsewhere, and only this much: one side-effect import line in
`ReturnCopilotPage.tsx` and `CaseOperationsPage.tsx`; one import + one spread in
`mocks/handlers/casePanelHandlers.ts`; one import + one spread in
`mocks/handlers.ts`; `ConversationPane.tsx` (in scope, brief item 6);
`copilotTokens.ts` (`support` group appended).

---

## 2. The payload shapes — what a contributor must send

`PanelSectionView.payload` is opaque, so this is the contract and nothing
validates it on the wire. **Keys are camelCase**, per AMENDMENT-7, and the reader takes **only** that
spelling — a wrong-cased payload renders a visible complaint rather than an empty
section. See §6.

### `support_return_records`

```jsonc
{
  "records": [
    {
      "returnRecordId": "…",            // joined onto CasePanelView.return_records[]
      "returnReference": "…",           // optional; the panel's own is preferred
      "artifacts": [
        {
          "artifactType": "RMA|TRACKING|LABEL|SHIPPING_INSTRUCTION|RETURN_LOCATION",
          "value": "…",                 // support-derived — see §5
          "status": "BOUND|AMBIGUOUS|UNMATCHED",
          "evidenceSpan": "…",          // support-derived
          "supportEventId": "…"
        }
      ]
    }
  ],
  "placement": {                        // one OBJECT, case-level, not per record
    "facilityId": "…", "bayId": "…", "reason": "…"
  },
  "unbound": [ /* artifacts that named no record, or one this case lacks */ ],
  "framingPromptKey": "support-multi-record-do-not-mix"
}
```

> **camelCase throughout, per AMENDMENT-7** — and note that the DTO field this
> joins onto, `CasePanelView.return_records[].return_record_id`, is snake_case.
> Two sources, two conventions; §6 has the rule and the test that pins it.

**`placement` is an object, not a list.** A one-element list is also accepted; a
two-element list reads as **no placement**, because picking the first would be
inventing which bay the goods are in.

**The cards are a join, not a copy.** Every record on
`CasePanelView.return_records[]` gets a card whether or not the section mentions
it — the record nobody has sent an artifact for is exactly the one an associate
is waiting on. A section record naming an id the panel does not hold is *also*
drawn: dropping it would hide a disagreement between two reads of one case.

### `support_thread_digest`

```jsonc
{
  "messages": [
    { "supportEventId": "…", "senderDisplayName": "…", "sender": "…",
      "status": "PROCESSED|PARKED", "intent": "…", "preview": "…",
      "recordedAtIso": "…" }
  ],
  "total": 23        // the THREAD's size, not messages.length — omit if unknown
}
```

`total` is deliberately separate. The digest is capped and the thread is not, so
"showing 5 of 23" is information that `5` alone does not carry. **Omit it rather
than sending `messages.length`** — the footer then says nothing instead of
claiming the cap is the total.

### `support_parked_messages`

```jsonc
{ "count": 3, "nlEnabled": false, "quota": 50, "oldestParkedAtIso": "…" }
```

`nlEnabled` **omitted** means the copy describes the count without asserting a
cause. "These are on file" is true either way; "free-text intake is switched off"
is a claim about a release the console has not read.

### `support_announcements`

No payload. The section need not be contributed at all — the renderer reads the
*other* sections out of `panel.sections` and draws an empty live region.

---

## 3. Design tokens

All under `COPILOT_TOKENS.support`. Every value is an M3 role; **no hex**, and
`text-xs` (0.75rem) is the floor. `supportTokens.test.ts` enforces all three.

| Token | Roles | Usage |
| --- | --- | --- |
| `card` / `cardHeader` / `reference` | `surface-container-low` on `outline-variant/40` | one record's card; matches `ProgressTruthPane`'s record card |
| `row` / `term` / `value` | `outline`, `on-surface` | a `<dl>` row. **`break-words`, never `truncate`** |
| `chip` + `chipTone.{neutral,attention,parked}` | container/on-container pairs | intent, disposition, binding status |
| `notice` | `secondary-container/40` | messages parked **on purpose** |
| `attentionNotice` | `tertiary/50` border, `tertiary-container/40`, **`on-surface`** | on file and waiting on a person |
| `warning` | `error/40` border, `error-container/30` | the do-not-mix framing |
| `digestRow` | `outline-variant/30` | one message in the digest |
| `systemEntry` / `systemEntryKicker` | dashed `outline-variant/60`, `surface-container-high/60` | the transcript entry |
| `liveRegion` / `announcer` | `outline` / `sr-only` | announcements |

**Three tones, three meanings, and they must not be collapsed.**

* `notice` (**secondary**) — on file, deliberate, needs nobody. `nl_enabled:
  false` parks rather than refusing; painting a working configuration in the
  error colour teaches an associate to discount the error colour.
* `attentionNotice` (**tertiary**) — on file and **unusable until somebody acts**.
* `warning` (**error**) — the do-not-mix framing, the one case that is not
  recoverable by reading the screen again.

**The `tertiary` pair is inverted relative to its siblings and it is a trap.**
`secondary-container` and `error-container` are light grounds with dark `on-`
foregrounds, so a `/40` tint lightens the ground and the pairing still reads.
`tertiary-container` is a **dark** brown with a **light** `on-` role: at `/40`
the ground moves *towards* the foreground and they meet at ~1.3:1. So
`attentionNotice` uses `on-surface` (~9.6:1) and the *solid* chip keeps
`on-tertiary-container` (4.54:1). `supportContrast.test.ts` computes this from
`tailwind.config.js` for every coloured token, because a class name cannot be
eyeballed for it.

> **Registered for V1:** `review.conflict` is
> `bg-tertiary-container/40 text-on-tertiary-container` — the same pairing, at
> the same tint. Not changed here (it is V1's token and V1's component), but it
> should be measured.

---

## 4. States, and what each one shows

| Section | Empty | Degraded (`status !== "ok"`) | Populated |
| --- | --- | --- | --- |
| records | renders `null` | a notice: display problem, nothing lost, back next refresh | `<h3>` + one `<h4>` card per record |
| *any, wrong casing* | — | a **distinct** notice: the payload is in the DTO's convention, this is a release fault, nothing about the return has changed | — |
| digest | renders `null` | same | `<h3>` + `<ul>`; footer only when `total` is given |
| parked | `count === 0` → `null` | same | `<h3>` naming the count |
| announcer | empty region | n/a | one sentence |

**Empty is not degraded, and the difference is the point.** "Support has told us
nothing about this return" and "we could not read what Support told us" draw
identically on a screen that shows neither, and only one is a reason to go and
ask somebody. That is why the backend registry catches a raising contributor
instead of failing the panel.

A value the platform has not been given draws as **`Pending`** — the domain's one
word for it, the exact spelling `ReturnCopilotFabrication`'s `??` rule allows.

**Content edge cases.** Long values wrap (`break-words`) rather than truncate: a
truncated tracking number is a *different* tracking number, and the panel is the
surface somebody opens to read one down a phone. `artifact.value` is bounded to
256 chars and `binding` to 128 by V2 phase 1b, server-side. Two artifacts of one
kind keep the order the platform recorded them in — two tracking numbers on one
return are two parcels, and reordering silently reassigns which is which.

---

## 5. Support-derived values: data, never markup

`artifact.value`, `evidence_span`, `preview` and `sender` originate in text the
platform did not write and reach an associate who acts on them. **Two defences,
answering two different attacks, and neither substitutes for the other.**

1. **Every value reaches the DOM as a React text child.** No
   `dangerouslySetInnerHTML`, no markdown renderer on this surface. `<img src=x
   onerror=…>` is characters.
2. **`readString` collapses whitespace runs**, in the one reader, with **no
   raw-string reader beside it**. Escaping stops a value becoming *markup*; it
   does nothing to stop it becoming *layout*. A tracking number submitted as
   `1Z999\nRETURN LOCATION: dock four` draws itself a second line shaped exactly
   like the labelled rows beside it, and an associate cannot tell a line the
   platform wrote from one the message drew.

Tested as **both halves, separately**, because each alone is green against the
wrong implementation: the literal present in the rendered tree *and* the element
absent from the document; and the card's **complete list of `<dt>` terms** pinned,
so an injected line cannot have become a row.

**Nothing support-authored is interpolated into the transcript entry.** The
sentence is built from a closed intent map plus the return reference — the one
identifier that must appear, because an update that would not say which return it
is about is unusable on a case with several.

---

## 6. Key casing — AMENDMENT-7, and it is enforced

**The DTO's own fields are snake_case** (`case_id`, `return_records`,
`section_id`, `accepted_commands`). **A section's opaque `payload` is
camelCase** (`returnRecordId`, `supportEventId`), mirroring the stored documents
it carries.

`readRecordsPayload` reads *both*, a few lines apart: the panel's
`return_records[]` in snake_case, the contributed payload in camelCase. That is
not inconsistency — two sources, two settled conventions — and the two blocks
look almost identical, which is exactly why they are pinned.

**The reader takes one convention, not both.** An earlier version of this module
accepted either spelling; that is removed. A tolerant reader draws a wrong-cased
payload perfectly and tells nobody the producer disagreed, and where it does not
draw, an empty section is indistinguishable from a case Support has said nothing
about. A wrong-cased payload now renders a **visible complaint** — see §4 — so
the disagreement is reported rather than absorbed.

**Enforcement:** `supportPayloadCasing.test.ts`. It hands each reader a
**recording proxy** and asserts the exact set of keys the reader actually asked
for — observed, not restated, because a list written beside the reader is two
copies of one intention agreeing with each other. Plus a behavioural guard: a
snake_case payload must read as **nothing**, which is the only assertion that
separates a strict reader from a tolerant one (a tolerant reader asks for the
camelCase key first and finds it, so the observed key sets are identical either
way).

## 7. The transcript system entry (DR-3)

`ChatHistoryEntry` gains `{role: "system", id, kicker, text}` — **no `author`,
no `statements`**. It is not a turn and the type says so.

* Drawn **full width and centred**. The associate's messages sit right and the
  agent's sit left; an entry borrowing either shape would put the platform's
  words in somebody's mouth on a screen somebody screenshots.
* Labelled **"Update from the platform"** in words, because position is not
  conveyed to a screen reader. Not "Support" — the platform wrote it.
* Dashed border, so the distinction survives a monochrome screen.
* **One entry per record on a fan-out**, each carrying the do-not-mix warning: an
  associate reading a single entry has no way to see there were others.

### The gap this phase does not close

**No endpoint serves these entries.** The relay writes them to
`state["systemEntries"]` on the conversation document;
`GET /api/v2/order-agent/conversations/{id}/transcript` serves `messages[]` and
`lastResultTurn`, and nothing in `frontend/`, `openapi/` or
`backend/src/return_platform/api/` mentions `systemEntries`. `readSupportSystemEntries`
therefore returns an empty list against every response the platform sends today,
and the transcript draws exactly as it does now.

The reader is written against the shape the relay actually writes and reads it
**defensively out of an `unknown`** rather than widening `ConversationTranscript`
with a field the API does not serve — declaring a field nothing fills is the same
class of thing as rendering a value nothing produced. **The missing half is one
field on that endpoint's response**, and it belongs to whoever owns it.

---

## 8. Accessibility

Audited against WCAG 2.1 AA. Every row is enforced by a test.

| Criterion | How it is met |
| --- | --- |
| **1.3.1 Structure** | `<h3>` per section, `<h4>` per record card and per sub-block; one `<dl>` per card, `<div>`-wrapped `<dt>`/`<dd>` only; `<ul>` for the record and message lists |
| **1.4.1 Use of colour** | Every chip carries its **word** inside it, with an `sr-only` prefix naming what the word is ("Status: BOUND"). The three notice tones are distinguished by their heading text as well as their ground |
| **1.4.3 Contrast** | Computed from the palette for every coloured token, at the opacity it is drawn with — `supportContrast.test.ts` |
| **2.1.1 / 2.4.7 Keyboard** | These sections contain **no interactive elements at all**. Nothing to reach, nothing to trap |
| **3.2.1 On focus** | Nothing changes on focus. **Arriving content never takes focus** |
| **4.1.3 Status messages** | Two live regions, both `aria-live="polite"`, both `sr-only`, neither focusable |

### The mid-edit rule, applied to arriving content

The panel polls every ten seconds, so an artifact lands between two keystrokes
while somebody is mid-sentence in a review draft. **Neither announcer is
focusable** — no `tabindex`, no `.focus()` — and the test asserts the *property*,
not the outcome: `document.activeElement` alone stays green against a `.focus()`
call on a `<p>` that cannot take focus, so it would be proving the element type.

**Both announcers are `aria-live="polite"` with no `role="status"`**, and that is
deliberate. The role is only a shorthand for the same implicit live region — but
`status` is already how the copilot's in-flight spinner identifies itself, and
three of V1's tests read `queryByRole("status")` as "no search is running". A
second one made that unanswerable.

**Silent on first sight.** A reader landing on a case, or replaying a past
return, must not be told that everything already on it has just arrived. The cost
is that the *first* system entry in a live conversation is drawn but not
announced; the panel's own region announces the same arrival.

---

## 9. What is still owed, and by whom

1. **A backend panel contributor for V2's three sections.** None exists —
   `register_panel_section("support_return_records", …)` and its two siblings are
   unwritten, so on a real deployment these sections do not appear at all. §2 is
   the payload they must produce. V2 phase 1 built the stores they read from
   (`DurableSupportIngressStore.parked_count` / `.list_parked` / `.list_inbound`,
   and the artifact facts).
2. **`systemEntries` on the transcript response** — §7.
3. **`review.conflict`'s contrast** — §3, registered for V1.
4. **No affordance on an unfiled artifact.** The section says one needs somebody
   to say which return it belongs to and offers no way to do it. That is correct
   ownership — V3 owns the clarification answer flow — but the two should be
   joined up: V3's clarification section and this one are describing the same
   artifact from two directions.
5. **Two shared lines with V3.** The `installSupportSections` imports in
   `ReturnCopilotPage.tsx` / `CaseOperationsPage.tsx`, and the `sections:` spread
   in `mocks/handlers/casePanelHandlers.ts`. V3 needs its own on both.
