# Order Discovery Prompt — Optimized Against the Running Platform

**Current as of 2026-09-09, revision 2.** Revision 1 was reviewed against the
code and ten of its claims did not survive; this revision corrects each and
says where the first one was wrong. It revises the earlier "Analysis and
Optimized Version" document against what the platform actually does and what
was measured on live Gemini and NVIDIA routes that day.

## 1. What the platform already does

The original analysis treats the prompt as one monolith. It is not. Every rule
is a named anchor in `backend/config/ai_gateway.yaml`, 22 of them, and five
stage tasks are alias lists over those anchors, chosen per turn by
`order_agent/reasoning_stage.py`:

| Stage | Chosen when | Anchors | Size |
|---|---|---|---|
| OPENING | no search has run yet | 14 | 11.2k chars |
| NARROWING | several candidates, all shown | 17 | 13.6k chars |
| WIDE | several candidates, page truncated | 19 | 15.7k chars |
| UNRESOLVED | a search found nothing | 14 | 10.9k chars |
| COMPLETING | one candidate, or `case_id` set | 13 | 9.1k chars |

The base task `ORDER_AGENT_REASONING_V1` carries all 22 (18.3k chars) and is
the one the alias anchors are *defined* in. Every stage prompt then gets the
`AgentAction` JSON schema appended (10.7k chars) and the temporal grounding
block (`model_gateway.py`, from `temporal_grounding_prompt`). A turn's system
prompt is therefore 20–27k chars, about 6–7k tokens, before the turn context.

Already true, which the original proposes as changes:

- The **searchable signals** are configured (`identification_fields`, with
  `intentKey`, multiplicity, aliases, `searchesOnlyWith`, and since today
  `knownValues`); `search_intent` carries them as extra keys by design.
- The **fact vocabulary** is configured in `config/returns/production.yaml`
  under `fields:`, in field groups. But — correcting revision 1 — the list the
  *model* sees is the hand-typed sentence in the `naming-a-fact` anchor, not a
  rendering of that config, and `captured_facts` only ever shows facts already
  captured. Adding a fact therefore takes a config line **and** a prompt edit.
- Customer confirmation and order confirmation are separate: the courtesy
  confirmation of a resolved customer is a `CLARIFY`; `CONFIRM_ORDER` binds to
  the candidate set the platform issued, and the candidate may be the customer
  row — `confirm_order` accepts an `order_reference` against the active
  customer candidate set, so no extra `ORDER_SEARCH` on the order number is
  needed first.
- Correction mode exists (`CORRECT_ACTION` with `validationError`), and since
  today the validator itself corrects four things the prompt used to only ask
  for: a repeated `CONFIRM_ORDER` after the case exists, a `CONFIRM_ORDER` the
  associate never agreed to, an `ORDER_SEARCH` with no signal, and a known
  colour left inside a product phrase.

## 2. Where the original analysis is wrong, with the evidence

### 2.1 "Supply the schema through structured output" — not for Gemini

Measured on the OPENING prompt with gemini-3.5-flash, same context, output
capped at 6,144 tokens:

| | calls | valid | order number in `search_intent` | thinking | time |
|---|---|---|---|---|---|
| `responseSchema` sent | 3 | 2 (the third ran to MAX_TOKENS) | 0 of 3 | none | 3 s; 19 s on the runaway, 183 s uncapped in production |
| schema in prompt only | 6 | 6 | 6 of 6 | 68–76 tokens | 2.6–3.3 s |

Constrained decoding cannot emit a key the schema does not list, and the
identifying signals are exactly such keys. A caveat the review is right to
add: this was measured against the *static* `AgentAction` schema; a schema
built per turn from `identification_fields` was not tried and might behave
differently. The runaway and the loss of thinking would remain. The provider
sends the schema only when `PLATFORM_GOOGLE_RESPONSE_SCHEMA=true`.

What is right in the recommendation: the schema the model sees is too big.
Shrink it (§4.2) rather than move it.

### 2.2 "Ask what is being returned on the following turn" — wrong turn

After `confirm_order` creates the case, the graph hands control back on the
**same** turn with `contextJson.case_id` set. A model given nothing re-sent
`CONFIRM_ORDER` fourteen times. The `after-the-confirmation` anchor now says
what is owed, and `validate_action` corrects a repeat.

### 2.3 The fact-contract gap is real, and its names are known

`return_reason` and `product_colour` exist. The case workflow already looks
for **`product_condition`** and **`return_quantity`** among the return-detail
facts (`workflows/return_case_activities.py`), and neither is in the configured
vocabulary nor in the `naming-a-fact` sentence. Revision 1 proposed
`item_condition`, which nothing reads. §4.1 gives the two-part fix.

### 2.4 `selected_candidate_id` has a strict use

It is required whenever a `query_plan` carries `candidate_set_id`
(`contracts.py`, the model-level validator). Keep it, and say that one use in
its description.

### 2.5 Aggregates are not set-scoped

Revision 1 told the WIDE stage to run a `COUNT` "over the candidate set". The
compiler never reads `candidate_set_id`, and a plan carrying one without
`selected_candidate_id` is rejected. An aggregate is scoped by its **filters**,
which is what the live `measuring-with-aggregates` anchor already says; and
its value is at `["rows","0",...]`, not `["count"]`, which is the page's row
count. The anchor stays as it is.

## 3. The optimized prompt, as edits to the 22 anchors

Design targets, in order: fewer contradictions, then fewer tokens. Nothing the
validator now enforces is repeated in prose beyond the one line that tells the
model the rejection exists. Implementation history goes. Every anchor is under
`PROMPT_SECTION_MAX_CHARS` (2,000).

### 3.1 Disposition of each existing anchor

| Anchor | Today | Disposition |
|---|---|---|
| `role-and-untrusted-input` | 324 | keep |
| `action-payload-contract` | 754 | rewrite → 3.2 (a) |
| `statement-and-identifier-rules` | 671 | keep |
| `when-to-search-instead-of-asking` | 779 | **merge** into 3.2 (b) |
| `identity-before-order` | 1,615 | **merge** into 3.2 (b) |
| `honouring-a-confirmation` | 901 | **merge** into 3.2 (b) |
| `after-the-confirmation` | 687 | keep (rule 1 of 3.2 (c)); keep in COMPLETING only |
| `choosing-the-next-question` | 631 | keep |
| `measuring-with-aggregates` | 1,218 | keep — correct as written, see §2.5 |
| `offering-values-and-confirming` | 713 | keep; it holds the `customer_id` rule |
| `graph-query-shape` | 777 | keep |
| `evidence-and-scope` | 396 | keep |
| `voice` | 976 | trim: drop "vary your openings, never repeat a sentence frame" |
| `candidate-pages` | 817 | keep; it holds the `customer_name_fuzzy` hedge |
| `paging-the-cached-search` | 931 | keep |
| `search-intent-fields` | 1,201 | trim: the misplacement rule is now the validator's; keep the key list and `searchesOnlyWith` |
| `carrying-the-search-forward` | 855 | keep |
| `reporting-observed-facts` | 931 | keep |
| `naming-a-fact` | 1,015 | edit: add `return_quantity`, `product_condition` (§4.1) |
| `not-asking-twice` | 512 | **merge** into 3.2 (b) |
| `source-system-escalation` | 1,155 | keep, UNRESOLVED only — the only path to an order placed this morning |
| `reading-the-transcript` | 461 | keep |

Net: four anchors (3,807 chars) become two (about 2,900), two are trimmed, one
is edited. The base task drops from 18.3k to roughly 17k chars; the stage
prompts by the same ~1.3k where they carried the merged four. The larger win is
§4.2, not the prose.

### 3.2 The rewritten and new anchors

**(a) `action-payload-contract`**, rewrite, replaces the current text:

```yaml
action-payload-contract: >-
  One action_type per turn, with its own payload, and the validator rejects
  the action without it: ORDER_SEARCH→search_intent, GRAPH_QUERY→query_plan,
  GET_SCHEMA→schema_entity_ids, CONFIRM_ORDER→order_confirmation
  (candidate_set_id, candidate_id, order_reference, order_line_references),
  REQUEST_ON_DEMAND_SYNC→strong_anchor_request with original_query_plan,
  RESPOND→response, CLARIFY→response with a non-empty requested_input holding
  the one question asked; REPLAN and OUT_OF_SCOPE carry none.
  business_capability is copied character for character from
  contextJson.compact_schema.capabilities and repeated in
  response.business_capability. A response that asks — a CLARIFICATION_QUESTION
  or a requested_input — cannot carry status COMPLETE or DISCOVERY_COMPLETE.
```

**(b) `identifying-the-customer-and-the-order`**, new, replaces
`when-to-search-instead-of-asking`, `identity-before-order`,
`honouring-a-confirmation` and `not-asking-twice` wherever they appear:

```yaml
identifying-the-customer-and-the-order: >-
  Settled details first. A detail the associate gives is settled when they
  give it, and a message that says confirm settles everything it names:
  "confirm Northgate Plumbing on account PHOENIX" settles both — report them
  on observed_facts, scope every later search and query to them (account_id
  beside customer_name CONTAINS), and never ask for them, or for a courtesy
  confirmation of them, again. Before asking anything, look for the answer in
  this message, in contextJson.captured_facts and in contextJson.transcript.
  Then, in this order. No customer resolved: ORDER_SEARCH with every signal
  the associate has given, on the first message, before any question; if the
  last search found nothing for those signals, do not repeat it or an
  equivalent — ask for one different, currently unprovided detail. Several
  customers: ask the one field that best splits them (see
  choosing-the-next-question); an order or PO number is the last resort, the
  associate has the customer in front of them and rarely the paperwork.
  Exactly one customer the associate has not confirmed: CLARIFY naming that
  customer and asking them to confirm — a question, not CONFIRM_ORDER.
  Customer confirmed: show that customer's orders and the products on them
  (see graph-query-shape), then narrow by product, date or delivery until one
  order stands. One order: show it so they can recognise it and ask; the
  validator refuses a CONFIRM_ORDER the associate has not agreed to on this
  turn.
```

**(c) The ladder's precedence**, stated in `after-the-confirmation` (kept) and
the two anchors above; no separate precedence anchor. The order is: the case
already exists (`case_id` set, COMPLETING only) → a `validationError` to repair
→ a signal needing its companion → a request for more of the same search →
settled details → the customer-then-order ladder in (b). Revision 1's row 5
had no action of its own; here "settled details" is a modifier on the ladder,
not a rung, and the confirming message reaches the ladder's last step.

**(d) `search-intent-fields`**, trim: delete the sentences about which key a
value belongs in; the validator lifts a known colour out of a product phrase
and refuses an empty intent, and the correction names the keys. Keep the
`intentKey` list, the `freeTextTerms` warning and `searchesOnlyWith`.

**(e) `voice`**, trim: delete "Vary your openings, never repeat a sentence
frame twice in one conversation". It adds nondeterminism and nothing else.

### 3.3 Stage alias lists after the edit

| Stage | Change |
|---|---|
| OPENING | replace the four merged anchors with (b) |
| NARROWING, WIDE | replace the three merged anchors they carry with (b) |
| UNRESOLVED | replace `when-to-search-instead-of-asking` and `not-asking-twice` with (b); keep `source-system-escalation` |
| COMPLETING | replace `honouring-a-confirmation` and `not-asking-twice` with (b); keep `after-the-confirmation` |

The base task keeps every anchor definition, including (b), so the aliases
resolve. Nothing is deleted from the base task until no stage references it.

### 3.4 What was cut, and why it is safe

| Cut | Why safe |
|---|---|
| Key-placement prose in `search-intent-fields` | `misplaced_signal` and `empty_search` correct it with the keys named |
| "Choose CONFIRM_ORDER only once they have agreed" as the sole guard | `unagreed_confirmation` enforces it; the sentence stays as the model's reason |
| Forced sentence variation | No business value; makes regression diffs noisy |
| The four overlapping ladder anchors | One anchor states the ladder in order |

Not cut, deliberately: `source-system-escalation`, the `customer_id` rule, the
`customer_name_fuzzy` hedge, `reading-the-transcript`,
`measuring-with-aggregates`. Revision 1 dropped the first four by omission.

## 4. Contract changes, ordered by evidence

### 4.1 Add the two missing return facts (config **and** prompt)

```yaml
# config/returns/production.yaml, under fields:
- field: return_quantity
  label: "quantity coming back"
  priority: 44
  customer_answerable: true
  field_group: RETURN_CONTEXT
- field: product_condition
  label: "condition of the item"
  priority: 43
  customer_answerable: true
  field_group: RETURN_CONTEXT
```

and the two names appended to the sentence in the `naming-a-fact` anchor.
These are the names `return_case_activities._RETURN_DETAIL_FACTS` already
reads, so once captured they reach the case. What they do *not* yet do is feed
the per-line selection or the returnable-quantity hold; that is a separate
change and this document does not claim it.

Until both edits land, "two of them, one damaged" is captured as nothing and
asked for again after the case opens.

### 4.2 Shrink the model-visible schema (~10.7k → ~6.5k chars)

Strip `description` from the embedded schema except on `action_type`,
`statement_type`, `selected_candidate_id` (§2.4) and the four payload objects'
one-line summaries; drop the docstring-derived paragraphs on `search_intent`,
`order_confirmation` and `observed_facts`. About 4k chars, roughly 1k tokens
per call — revision 1 overstated this. The change is where `schema_str` is
built in `structured_invocation.py`.

### 4.3 Do **not** make `response.status` an enum

Revision 1 proposed one. `evidence.py` keeps `TERMINAL_STATUSES` as a string
set on purpose — "status is the model's word and older releases spell it
differently" — and matches with `.strip().upper()`. Stored turns are
re-validated from storage on replay and resume, and six existing tests build
responses with other spellings. An enum breaks replay of old conversations for
no gain the loose match does not already give. If anything, log an unknown
status once per turn.

### 4.4 Do **not** convert `AgentAction` to a discriminated union

Revision 1 said it would not change what the model sees. It would: the prompt
embeds the cleaned schema, the cleaner flattens `anyOf` and drops `$defs`, so a
union emits `oneOf` with a discriminator mapping pointing at deleted
definitions, and the schema grows to nine full variants. The payload-per-action
rule is already enforced by `AgentAction.validate_action_payload`.

### 4.5 Keep `selected_candidate_id`

Required when a `query_plan` names a `candidate_set_id`. Say so in its
description, and keep that description through §4.2.

## 5. Regression cases

The original's twelve cases stand. Added, from what failed live on 2026-09-09:

| Case | Setup | Expected |
|---|---|---|
| 13 Confirmed already | `case_id` set | `CLARIFY` for reason, condition, quantity; a `CONFIRM_ORDER` is corrected |
| 14 Signals survive | "return something from order CO363355" | `search_intent.orderNumbers == ["CO363355"]` — fails under Gemini constrained decoding |
| 15 Date window | "WESTFIELD on NASH, last 30 days", three orders in the window | `GRAPH_QUERY` with `order_date` bounds from `resolvedDateWindows.last_30_days` returns three |
| 16 Customer confirmed is not order agreed | "Confirm the customer X on account Y" after the agent named an order | `CLARIFY` showing the order; a `CONFIRM_ORDER` is corrected |
| 17 Empty search | "black ABS DWV vent ell" | no `ORDER_SEARCH` runs with an empty intent; the correction names the keys |
| 18 Output bound | every reasoning task | `maximumOutputTokens` is set (6,144); a runaway ends there, not at the model ceiling |
| 19 Route order | one Google key rate-limited | the other Google keys are tried before any NVIDIA route; `maximumTotalAttempts` covers them all |

Case 18 bounds output, not wall-clock: a slow provider is bounded by
`PLATFORM_AI_TIMEOUT_SECONDS`, not by this.

## 6. Order of work

1. §4.1 — two config lines and one prompt sentence; restart.
2. §3.2 (a), (b), (d), (e) into `ai_gateway.yaml`, then §3.3's alias lists;
   validate the file loads; run the nineteen cases in manual mode, then live.
3. §4.2 schema shrink — one function.
4. Leave §4.3 and §4.4 undone.

The principle from the original holds: one rule, one owner. The correction is
about *which* owner. The model decides intent. The validator enforces shape
**after** generation, because enforcing it *during* generation, on this model,
deleted the intent — and four of the rules that mattered most today are now
validator rules, which is why the prompt can get shorter without getting
weaker.
