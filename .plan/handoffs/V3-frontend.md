# Handoff — V3 frontend: the clarifications section and the reply review

Supersedes the "not written, blocked at the seam" note in `.plan/handoffs/V3.md`.
That halt was correct: item 6 contributes through V1 phase 2's registry, which
did not exist on V3's base. This phase branches on `30b61a1` (V1 phase 2's head
`aa1f261` merged in) and builds against the real seam.

**Read §1 before anything else.** It is the only part of this document that
changes what somebody else has to do.

---

## 1. The seam, and where V1's own documents disagree

`CasePanelView.clarifications[]` is declared on the frozen DTO and attributed to
V3. **Nothing can fill it.**

- `api/case_panel.py:110` sets `clarifications=()` as a literal.
- The contributor Protocol (`operations/case_panel.py:222`) is
  `async (context) -> PanelSectionView | None`. It returns a section. It has no
  way to write a top-level DTO field.
- V1 phase 2's handoff §2 says the field arrives "through the section registry",
  which the registry cannot do.

Contracts §9 names both vehicles too — line 104 says *panel section*, line 106
declares the DTO field. Only the section is buildable.

**What the console does.** `readClarifications(panel, section)` reads the section
payload first and `panel.clarifications` second, de-duplicated on
`clarificationId`, section order preserved and the field's extras appended, first
writer of an id winning. Whichever vehicle the integration pass wires, the
section draws; neither one going dark is silent.

**What is owed on the backend** (and is *not* in `.plan/merge.md`'s V3 list —
add it):

```python
# in V3's own module, at import time
register_panel_section("clarifications", contribute_clarifications)
# contributor returns PanelSectionView(
#     section_id="clarifications",
#     status="ok",
#     payload={"clarifications": [ <the support_clarification_requested fact values> ]},
# )
```

The payload's entries are the fact values exactly as
`operations/return_support/message_classification.py:638` writes them —
camelCase, ten keys. `frontend/src/mocks/handlers/casePanelHandlers.ts` carries a
worked example.

Until that contributor exists the section is correct and invisible. That is the
honest state and it is why this document leads with it.

---

## 2. What was built

| Module | Owns |
| --- | --- |
| `api/caseClarifications.ts` | the answer endpoint's client, hand-typed (see §6) |
| `panes/casePanel/sections/clarificationModel.ts` | what a clarification is; the candidate join; the two producers' vocabularies |
| `panes/casePanel/sections/ClarificationsSection.tsx` | the card, the map-or-reject fieldset, the answer form, the confirmation |
| `panes/casePanel/sections/registerClarificationsSection.ts` | the one line that puts it on the panel |
| `panes/casePanel/sections/supportReplyDraft.ts` | what a `SUPPORT_REPLY` draft is |
| `panes/casePanel/sections/SupportReplyBody.tsx` | the reply's body, provenance and disclosure caution |
| `mocks/handlers/caseClarificationHandlers.ts` | a **strict** mock of the unmounted endpoint |

Two of V1's files changed, minimally: `TemplateReviewSection.tsx` switches on
`review_kind`, and `CasePanel.tsx`'s read-only audit view does the same.
`copilotTokens.ts` gained one token. `main.tsx` gained one call.

---

## 3. Design tokens

Everything is `COPILOT_TOKENS`. No hex, nothing below the 0.75rem floor.

| Token | Where |
| --- | --- |
| `review.clarification` **(new)** | the card. `secondary-container`, matching what `state.OPEN` already uses the secondary pair for: an open item addressed to the reader |
| `review.conflict` | the confirmation prompt, and the disclosure caution |
| `review.gap` | refusals only — an error, never a caution |
| `review.field.{row,label,value,input,edited}` | the `<dl>` rows and both textareas |
| `review.action.{primary,secondary,bar}` | every button |
| `review.provenance` | what-we-tried chips; the reply's rung, confidence and citation chips |
| `review.liveRegion` | the arrival announcement — reserves its own height, so announcing shifts nothing |
| `typography.{subheading,body,caption}` | heading, quoted question, hints |

**`review.clarification` is new rather than borrowed.** The first draft used
`review.conflict`, whose documented meaning is "another actor editing; a
superseded draft; a confirmation". A question from a supplier is none of those,
and reusing the nearest container is how a token's meaning erodes until it means
"boxed".

One non-token literal: `CHOICE_ROW = "flex items-start gap-2 py-2
min-h-[2.25rem]"`, sized so a radio is a comfortable target. Promote it if a
second surface needs choice rows.

---

## 4. States

### Clarifications section

| State | What shows |
| --- | --- |
| no clarifications | **nothing** — `return null`. A permanent "Support is not asking anything" is furniture reporting an absence on the great majority of returns |
| `section.status === "degraded"` | *"Could not check whether Support is waiting on an answer. This will be retried automatically."* — not silence: "has not asked" and "we could not find out" are different, and only one means go and read the thread |
| one or more | heading, live region, one `<article>` per clarification |
| unanswerable entry (no id or no question) | skipped, never drawn as an empty card, and never thrown on — the console half of the backend registry's degrade-don't-crash promise |
| answered | the form is replaced by a receipt; the card stays |

### The card

Quoted question → `<dl>` (why / what we need / what we tried) → artifact evidence
if `MAP_OR_REJECT` → the form.

Missing scalar values read **"Unavailable"**, which is this domain's existing word
(`ReturnCopilotFabrication.test.ts` enforces the vocabulary; do not add a seventh).

### The form

| Step | Behaviour |
| --- | --- |
| radios | `MAP_OR_REJECT` only. The case's own records plus "None of these". **Never a free-text record box** — a loose artifact creates no record (§4), so a typed RMA is either one already offered or a wrong one silently accepted |
| answer | `<textarea>`, `maxLength={4000}`, label "How do you know?" when binding, "Your answer" otherwise |
| first press | validates; on failure a `role="alert"` refusal, also in the field's `aria-describedby`; on success the confirmation opens and **nothing is sent** |
| confirmation | reads the decision back: *"Attach {value} to {RMA}, and send your answer to Support?"* / *"Tell Support this belongs to none of the returns on this case?"* / *"Send your answer to Support?"*. Escape backs out and the typed answer survives |
| second press | posts; `202` → receipt |
| receipt | *"Your answer is recorded. Support will be told."*, or on `duplicate` *"This was already answered. The answer on file stands."* |
| refused | the endpoint's own words, never a status code. An associate shown "409" presses the button again |

The four client-side refusals mirror the server's and replace none of them.
`CLARIFICATION_MAP_WITHOUT_RECORD` is the one that is not a convenience.

### Reply review

Rendered by `TemplateReviewSection` with the body swapped. Heading "Reply to
Support"; the template's subject/sections `<dl>` and raw-write toggle are not
drawn, because a reply payload has none of them.

| State | What shows |
| --- | --- |
| `OPEN` | the reply in an editable `<textarea>` **seeded from `messageText`** |
| any other state | the reply as read-only `whitespace-pre-wrap` text |
| `messageText === ""` | *"This reply is empty. Rebuild it before sending — Support would receive nothing."* |
| edited and `disclosesAgent` | a caution to keep the platform-disclosure line |
| audit view | heading and the reply text, read-only |

Provenance chips: the rung that answered, the resolver's own confidence as a
percentage (never a word — the thresholds live in the release), the citation
count, and whether the text discloses the agent.

---

## 5. Accessibility

**Arrival never takes focus.** `role="status" aria-live="polite"`, one region per
section, derived during render rather than in an effect so the announcement does
not arrive after the thing it announces. Nothing is announced on first paint. The
test types into the review draft beside the section, forces a poll, and asserts
the announcement *and* that the caret and the keystrokes stayed put.

**The confirmation is the one place focus moves**, because the associate asked
for it by pressing a button. Escape returns them.

**Keyboard, end to end**: radio (Space) → textarea (Tab) → submit (Tab, Enter) →
confirm (focused, Enter). Asserted, with no pointer anywhere in the path.

`aria-disabled`, never `disabled`: a disabled submit leaves the tab order, so a
keyboard associate finds nothing there and no way to learn why.

Native `<fieldset>`/`<legend>` and explicit `for`/`id` throughout;
`aria-labelledby` over `aria-label` wherever the label is on screen.

**Known and deliberate:** `<section aria-labelledby>` creates a `region`
landmark, and the guidance is to use `region` sparingly. Kept because V1's review
section does the same and two landmark conventions on one panel is worse than one
extra region. Revisit for the whole panel, not for this section alone.

---

## 6. The endpoint that is not mounted

`POST /api/v1/cases/{case_id}/clarifications/{clarification_id}/answer` is
written and tested and **absent from `main.py`**. So:

- `api/caseClarifications.ts` transcribes `ClarificationAnswerRequest` (three
  fields) and `ClarificationAnswerAcceptedView` (six) by hand. The model is
  `extra="forbid"`; one wrong key name is a production 422 that no permissive
  mock finds.
- `caseClarificationHandlers` is a **third** MSW array. The panel's contract test
  asserts every route in *its* array is published, in both directions; folding
  this one in would break a check that is working.
- The mock is strict — it refuses unknown keys, a `map` with no record, and an
  over-length answer, exactly as the server does.
- `caseClarifications.contract.test.ts` **fails the day the route appears in the
  committed OpenAPI**, and its failure message names the three things to do.

That test is the deletion notice for everything in this section. Do not silence
it; satisfy it.

---

## 7. What a reviewer should try to break

1. **Feed a script tag through `verbatimQuestion`, `artifactValue` and
   `evidenceSpan`.** Assert the whole rendered string as an equality *and* the
   element as absent. Either alone passes for a wrong reason.
2. **Add a neutraliser** that strips `<`/`>` or rewrites `:`. The element-absence
   assertions all still pass; the ten-sentence equality is what fails. Verbatim
   is a contract requirement, and over-neutralising is the failure that looks
   like safety.
3. **Point `readClarifications` at `panel.clarifications` alone.** Three of
   fourteen model tests fail. Eleven stay green — that is how invisible this was.
4. **Make `registerClarificationsSection` a no-op.** 18 of 19 section tests fail;
   the survivor is the one that renders nothing.
5. **Make the kind switch sniff `payload.messageText`** instead of `review_kind`.
   Everything stays green except the one test that hands a template review a
   `messageText` key.
