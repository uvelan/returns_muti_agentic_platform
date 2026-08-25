# ORDER_AGENT_REASONING_V1 — prompt version history

Moved verbatim out of `backend/config/ai_gateway.yaml`, where these notes sat as
~350 lines of comments above the task definition and had to be scrolled past on
every prompt review. The operative rules live in the YAML's
`systemPromptSections`; this file is the record of how they got there. Entries
are newest-last, matching the order they were written in the YAML.

## v14 (2026-08-15/16) — published as release `confirm-customer-first-v14`

* **Tone.** Rewritten from "concise, structured, consistent" to a warm
  colleague's voice, and the two overlapping tone passages the earlier edit left
  behind are now one.
* **Progressive narrowing.** The first attempt at this replaced one bad rule
  with a worse one — "confirm a step at a time: customer, then product, then
  order" — which is a script, and what to ask next does not depend on a script.
  It depends on what the associate already gave and on which field actually
  splits the candidates still in play. The prompt now teaches that as reasoning,
  and names the operations that measure it: COUNT, GROUP_BY, TOP_VALUES,
  DISTINCT_VALUES and MISSING_VALUE_COUNT have been compilable since
  `cypher_compiler.py` was written and no prompt had ever mentioned one, so the
  agent could rank a question by `suggested_discriminators` but never measure
  one itself. COUNT_DISTINCT, MIN, MAX, SUM and AVERAGE are named as forbidden
  rather than offered: `SchemaQueryGuard` requires `capabilities.aggregatable`
  and no field in the active schema sets it, so a plan using one is a guard
  rejection and a wasted correction attempt.
* **Bounded by construction.** An aggregate is a page like any other: small
  limit, at most five values named in a question, and never a value the
  evidence did not return — the same stance the candidate-page rules already
  take, written once and applied to both.
* `customer_id` is never asked for or shown. Scoping on `account_id` with
  `customer_name CONTAINS` returns the same record, proven on a live run.
* `delivered_at` corrected to `shipped_at`: `sales_order` has no `delivered_at`,
  so the worked example named a field that does not exist.

**Length.** `TaskConfiguration.systemPrompt` was capped at 12,000 characters and
this prompt sat 67 short of it. ~1,650 characters of duplicated prose came out
(two tone blocks merged, the traversal and continuation passages compressed,
four restatements of "only name what the evidence contains" reduced to one); the
narrowing and aggregation rules cost ~2,750, so the prompt is ~1,100 longer than
before at 13,016. The cap is now 14,000, which is a budget and not a limit: the
assembled prompt is the configured string plus the response schema plus a
temporal addendum — ~22,200 characters before this change — and
`maximumInputTokens` for this task is 32,000, of which a turn spends ~16,750,
most of it `contextJson`. A prompt would have to pass roughly 60,000 characters
before it threatened that allowance.

## v15 (2026-08-16) — the payload contract, disclosed

A 56-call evaluation across eight models found four of them, across two vendors,
producing correct answers that the platform then rejected for a rule it had
never stated. `AgentAction.validate_action_payload` requires a `response`
carrying a non-empty `requested_input` for `CLARIFY`, and nothing said so: the
emitted schema's top-level `required` is the three unconditionally-required
fields, `requested_input` is an optional string, and the conditional cannot be
written in the JSON Schema dialect these contracts are emitted in (Gemini's
`responseSchema` is an OpenAPI 3.0 subset with no `if`/`then` or
`dependentRequired`, and OpenAI's strict mode rejects both keywords as well).
`gemini-2.5-flash` asked five real customers apart by name, obeyed the
five-value offer cap, and lost the turn to `missing payload for action type
CLARIFY`; one model leaked its reasoning weighing "make it a
CLARIFICATION_QUESTION statement" against "put it in requested_input" and
guessed.

So the rule is now stated in two places that reach every provider: as prose in
the prompt, and as `description` text on `AgentAction.action_type` and
`AgentAction.response` (see `order_agent/contracts.py::_PAYLOAD_CONTRACT`),
which `clean_gemini_schema` was separately dropping on the floor — it returned a
`$ref`'s definition and discarded the field's own siblings. Neither validator
was touched: this is disclosure, not relaxation.

* The per-action payload table, added after "no Markdown or extra keys".
* `expected_value`, previously enforced by `HallucinationGuard` and mentioned by
  no prompt: it is compared literally against the value already at
  `result_path`, so it is left out unless it is being copied exactly.
  `nemotron-49b` lost a scenario writing a prose placeholder into it.
* "a GRAPH_FACT carrying no evidence_refs is rejected, on any action", appended
  to the sentence that already asked for cited evidence. That rule
  (`ResponseStatement.validate_evidence_shape`) was stated only as "cite exact
  query evidence" and only in a sentence about RESPOND, and measurement showed
  models dropping the refs while getting everything else right. It is disclosed
  in the schema now as well, on `ResponseStatement.statement_type`.

**Measured, not assumed.** `gemini-2.5-flash` on the five-Alvarado clarification
scenario: 0 of 1 under v14 (`missing payload for action type CLARIFY`), 3 of 3
under this text, with `requested_input` populated on every CLARIFY sampled. An
intermediate revision that rewrote the evidence sentence into field-by-field
mechanics instead of extending it cost `nemotron-49b` a scenario it had passed,
and was reverted; the wording is v14's sentence with a clause added, not a
replacement for it.

**Length:** 13,924 of 14,000. Three passages of genuinely duplicated prose came
out to pay for it: "and the guard refuses every plan that uses one on a field
that is not" (the same sentence already says the guard requires `aggregatable`),
the standalone "Warmth never licenses less accuracy" (immediately preceded by
"never let warmth displace a fact or soften a limit"), and "so the number is
needed neither in a question nor in an answer" (immediately preceded by "Never
ask for or show a customer_id"). No rule was dropped.

`promptVersion` is bumped rather than amended in place. v14 is published as
release `confirm-customer-first-v14` and has attempts stamped against it;
editing its text would make those traces cite a prompt that no longer exists.

## v18 (2026-08-20) — one prompt, written as twenty-one

The 14,699-character single string this replaces instructed the model on eight
unrelated concerns at once — the action and JSON contract, narrowing strategy,
aggregation operations, the identity rules, tone, evidence citation, candidate
paging, scope refusal — with no structure a reviewer could point at and 301
characters of headroom, so every rule added since v14 had been paid for by
deleting or compressing an older one. The v14–v15 notes above record exactly
that: passages added, then rewritten, then partly removed for space.

`systemPromptSections` is how the prompt is now *written*. It is not a new
runtime concept: `TaskConfiguration._compose_system_prompt` joins the sections
in declaration order into the same `systemPrompt` field every caller already
reads, so the released payload, the request digest, the provider call and the
response contract are all unchanged. Order is meaning — the role and the
untrusted-input framing have to come first — which is why these are an ordered
list and not a mapping. A section name never reaches a model; it is what makes a
prompt edit reviewable as a diff against one concern instead of against a wall
of prose.

Eighteen of the twenty-one are v17's own sentences, in v17's order, cut at the
concern boundaries and otherwise untouched. Three carry rules that were not
there, and each closes a defect observed against the running platform on
2026-08-20:

* **`reporting-observed-facts`** (rewritten from v17's fact paragraph). Three
  turns of a live conversation emitted no observed_facts at all, so
  `_capture_observed_facts` had nothing to merge, the extracted-facts panel
  stayed empty, and every turn re-ran the same search and re-asked the same
  question. v17 asked for what the associate says "about the return itself",
  which reads as excluding the identifying details, and it named CONFIRM_ORDER
  nowhere near the requirement. Now: every stated or confirmed detail,
  identifying ones included, on every action.
* **`naming-a-fact`** (rewritten). v17 said the name was "a short stable name"
  and left the model to invent one. `FactCatalogue.capture` discards any name no
  `clarification_policy.fields` entry claims — it logs
  `order_agent_unconfigured_observed_facts` and drops the fact — so an invented
  name is a fact silently lost. The vocabulary is spelled out because nothing in
  `contextJson` carries it: `identification_fields` describes search signals,
  and `captured_facts` only lists what a capture already succeeded on.
* **`honouring-a-confirmation`** (new). "Confirm the customer <name> on account
  <account>" was sent twice and answered both times by asking which branch and
  re-listing the same five accounts. v17 did say "never ask again for something
  the associate has effectively given" — as a principle, inside the narrowing
  paragraph, and it was not followed. It is now its own rule about the concrete
  case, and it does not touch the identity-first ladder above it: that governs
  what to ask while several candidates remain, this governs what has stopped
  being a question.

**Budget.** The number that governs an edit is now per concern:
`PROMPT_SECTION_MAX_CHARS` is 2,000 and the largest section is well inside it.
`TaskConfiguration.prompt_budget` bounds the composed whole at the sections' own
budgets, so growing the prompt means either growing a named concern or adding
one — both visible in a diff — rather than wedging a sentence into the middle of
a wall and hoping 301 characters is enough. The old flat 15,000 still applies to
every task that writes its prompt as one string.

`promptVersion` is bumped rather than amended for the reason v14 gives — v17 is
published and has attempts stamped against it.

## Stage prompts (v19 era)

The same twenty-one sections, in the same order, subset per conversation stage.
Every stage task's text is a YAML alias (`*section-name`) to the anchor defined
once in `ORDER_AGENT_REASONING_V1`, so there is exactly one copy of every rule:
editing a section there changes it for every stage that carries it, and a stage
cannot drift from the complete prompt because it has no text of its own to
drift with.

**Why stages at all:** the complete prompt is 17,109 characters and adherence at
that size is visibly poor — an explicit "confirm the customer X on account Y"
was answered by asking which location, twice, with the rule against exactly that
present and correct. Most of those characters do not apply to most turns.
`order_agent/reasoning_stage.py` reads the stage off the turn's own state
(`orderSearchCache`, `case_id`) and `model_gateway` picks the task.

Per-stage rationale (why each carries and omits what it does):

* **OPENING** — no order search has run (`orderSearchCache` absent; equally the
  turn after a REPLAN, which clears the cache). Nothing can be narrowed:
  no candidates, no page to advance, no aggregate worth measuring, no order to
  confirm. The turn searches on whatever it was given — and then asks the first
  narrowing question inside the same turn, which is why `identity-before-order`
  and `choosing-the-next-question` are carried: without them it reached five
  candidates with no rule for what to ask and fell back to "do you have an order
  number?", the one question the ladder exists to avoid (observed live against
  six customers sharing a surname, where the first name was the field that
  split them).
* **NARROWING** — several candidates, all on the table (`shown` ≥ `totalFound`).
  The stage the identity ladder is for, and the one the live defect happened in.
  Carries `identity-before-order` whole, including dd2a5fc's correction. What it
  does not carry is the two rules that need a truncated page — measuring past it
  with an aggregate, and serving the next page from the cache — because neither
  can apply when the associate is already looking at every match.
* **NARROWING_TRUNCATED (WIDE)** — more records matched than were returned
  (`shown` < `totalFound`). Everything NARROWING does, plus the only two things
  this state permits: measuring past the page with COUNT/GROUP_BY/TOP_VALUES,
  and serving the next page from the cache. The largest of the five, and the
  rarest.
* **UNRESOLVED** — a search ran and matched nothing (`totalFound` is 0). Not a
  narrowing problem, so not a narrowing prompt: nothing to discriminate between,
  no candidate to name. The graph is a periodic projection, so what this turn
  owes the associate is the source-system escalation — and, when no anchor can
  be filled, an honest account of what was searched. The only stage carrying
  `source-system-escalation`; keeps `when-to-search-instead-of-asking` for the
  half of that rule which forbids running the same empty search again.
* **COMPLETING** — one candidate stands (`totalFound` is 1), or an order is
  already confirmed and `case_id` is set. Asking which customer is over. What is
  left is showing what is on the order — which is what `graph-query-shape`
  exists for — and taking the confirmation. The smallest of the five.

Every stage carries the core: the role and untrusted-input framing, the action
and JSON contract, the statement rules, fact reporting and naming, what not to
ask twice, evidence and scope refusal, the voice, and the transcript. Nothing
drops a rule that could apply to the turns it serves; a stage that is wrong
about that is a guard rejection, not a shortcut.

`ORDER_AGENT_REASONING_V1` stays complete and stays the fallback. A deployment
running a release cut before the stage ids existed — `runtime-configuration-
init` in compose.yaml still publishes with `--if-missing` — resolves none of
them and degrades to the full prompt rather than failing to start.

## Section-level notes

* `identity-before-order` — commit 6a295b4. Who the customer is comes before
  which order, and the questions that say WHICH customer are the contact_point
  details. The first version listed the identity fields in a fixed order —
  name, then phone, then email — and left out the branch account entirely.
  Asked to find an order for a customer whose five candidates differed ONLY by
  branch, the agent dutifully asked for a phone number, which could not split
  them. Identity still comes first; which identity field to ask for is measured.
* `honouring-a-confirmation` — live defect, 2026-08-20: an explicit confirmation
  naming both customer and account was answered twice by re-asking which branch.
  What the associate has given is settled; look before asking.
* `reporting-observed-facts` — live defect, 2026-08-20: three turns, no facts
  emitted, so nothing persisted and each turn re-ran the same search. Every
  stated detail, on every action — identifying ones too, not only the return's
  own.
* `naming-a-fact` — `FactCatalogue.capture` discards any name no
  clarification_policy field claims, so an invented fact name is a fact silently
  lost. The vocabulary is named in the prompt because nothing in contextJson
  carries it.

## Model-context note (`modelContexts`)

`nvidia/nemotron-mini-4b-instruct` is pinned at 4,096 context tokens: every
`ORDER_AGENT_REASONING_V1` call to it returned "HTTP 400 — This model's maximum
context length is 4096 tokens, however you requested 24014 tokens"; that task's
`maximumInputTokens` is 32,000, so the route was never capable of serving it.
Observed across a 56-call evaluation, 2026-08-16.
