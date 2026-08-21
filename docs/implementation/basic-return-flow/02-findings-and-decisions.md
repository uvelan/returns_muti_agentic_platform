# 02 · Findings and decisions

Append-only. Never rewrite an entry; supersede it with a new one.

---

## F-1 · The Support Chat message renderer collapses every line break

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `frontend/src/domains/support/SupportConsolePage.tsx:1472`,
  `function Message`, rendering `{message.messageText}` into a `div` classed
  `max-w-[80%] break-words rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed`
  with no `whitespace-pre-wrap`.
- **finding:** `messageText` is plain text carrying newlines
  (`return_support/service.py:301` copies `supportDraft` verbatim into message
  sequence 1). HTML collapses runs of whitespace, so a sectioned template renders
  as one continuous paragraph. Phase 9 requires sections, labels, line breaks and
  hierarchy to survive.
- **decision:** render the message with preserved whitespace, and additionally
  carry the structured business data in `businessPayload` so the UI never parses
  the text back into fields.
- **affected files:** `frontend/src/domains/support/SupportConsolePage.tsx`, plus
  the work-item creation path for `businessPayload`.

---

## F-2 · The support draft names no business fact a human could act on

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `backend/src/return_platform/agents/return_workflow.py`,
  `ReturnWorkflowAgent.assess`, the `support_draft = (...)` expression.
- **finding:** the draft is built from `sessionId`, `orderSource`,
  `productPresence`, method, and per-item `orderLineId` / `productId` / quantity
  / reason. It contains no customer name, no product name, no colour, no order
  number and no bay assignment -- only internal identifiers. This is the
  "vague support message" complaint, and a Phase 8 violation.
- **decision:** the visible Support message must be composed from authoritative
  projected case state against the template frozen in the directive, and the same
  values persisted structurally.
- **affected files:** to be determined once the case-path draft origin is
  confirmed (`return_case_activities.py`, `DraftSupportRequestInput`), because C1
  establishes the Copilot page does not use `associate_flow`.

---

## F-3 · Two support-handoff paths exist; only one is live for this flow

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `operations/associate_flow.py::submit_details` calls
  `ReturnSupportService.create_work_item` directly;
  `workflows/return_case_workflow.py::_open_support` (1297) is the case path,
  reached through `OpenSupportWorkItemInput` (660).
- **finding:** editing `associate_flow.py` would change a path the Copilot page
  never calls, and the change would appear to do nothing at runtime.
- **decision:** all work targets the case path. `associate_flow.py` is left alone
  unless evidence shows the live flow reaches it.
- **affected files:** none -- this is a routing decision that prevents a wasted
  edit.

---

## F-4 · The Windows host launcher starts a worker that cannot exist, and omits one that must

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:**
  - `scripts/run_all_host.ps1` started `@("temporal", "orchestrator", "outbox", "jobs", "integration-outbox")`.
  - `scripts/run_worker_host.ps1` accepted only
    `ValidateSet("temporal","orchestrator","outbox","integration-outbox")` -- `jobs`
    fails parameter binding before the script body runs.
  - `scripts/linux/09_start_workers.sh` already carries the corrected set and the
    reason: *"`jobs` is gone: the data-console package it imported was deleted...
    `discovery` is added because it is a REQUIRED_PROCESS_CLASS"*.
  - `configuration/process_adoption.py:62-71` lists `order-discovery-worker` in
    `REQUIRED_PROCESS_CLASSES`.
- **finding:** two separate failures in one line.
  1. `jobs` dies instantly. `run_all_host.ps1`'s monitor loop treats *any* child
     exit as fatal and tears down the whole stack, so the Windows launcher could
     not hold a running system at all.
  2. `order-discovery-worker` was never started on Windows. It is required for
     release adoption, so `GET /api/config/adoption` could never leave
     `ACTIVATING` -- with no error anywhere to read.
- **decision:** bring the Windows scripts to the set Linux already validates:
  drop `jobs`, add `discovery`, and add `housekeeping` to the accepted set (its
  script `backend/scripts/run_housekeeping_worker.py` exists and only Linux could
  reach it). Name each child in the launcher so the fatal-exit message says which
  process died instead of printing a bare PID.
- **affected files:** `scripts/run_all_host.ps1`, `scripts/run_worker_host.ps1`.
- **validated:** V-5 -- adoption reached `LIVE` with `pending_process_classes: []`
  and `order-discovery-worker` adopted.

---

## F-5 · No seeded order's warehouse has a single configured bay

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:**
  - SQL `platform.bay_configuration`: **6 rows, every one `warehouse_id =
    'WH-CHENNAI-01'`, `branch_id = 'BR-CHENNAI'`**. Their only writer is the
    `MERGE` at `configuration/sql_migrations/002_domain_models.sql:134`.
  - Neo4j: `MATCH (b:Bay) RETURN DISTINCT b.warehouse_id` -> `WH-CHENNAI-01` x24;
    `MATCH (w:Warehouse)` -> 4 nodes, all `WH-CHENNAI-01`.
  - Seeded orders carry numeric warehouse ids: `sell_warehouse_id` /
    `ship_from_warehouse_id` of `686`, `1969`, `1305`, ... (11299 `SalesOrder`
    nodes).
  - `return_source.warehouseMaster` holds 24 warehouses **with** bays (e.g. `686`
    = "Louisville Distribution Center", 12 bays), but
    `backend/scripts/generate_seed_data.py::_generate_warehouses` documents it as
    *"Provisional -- nothing reads this"*, and
    `backend/scripts/add_warehouse_bay_entities.py` states plainly that the
    descriptor projects `warehouse` and `bay` **out of `platform.bay_configuration`**
    because at the time it was written no warehouse master existed.
- **finding:** the two warehouse universes are disjoint. Every seeded order points
  at a warehouse the bay pipeline has never heard of, so
  `observe_eligible_bays(warehouse_id="686")` returns nothing and
  `BayAssignmentAgent.assess` can only answer with an empty `eligibleBayIds`.
  Bay assignment on the seeded corpus is therefore **structurally unresolvable** --
  not intermittently, always.
- **decision:** deferred to D-1 in this file. Fabricating a bay is forbidden and
  hiding the gap behind an "unresolved" banner would make the normal path
  permanently degenerate, so the gap is closed at its source rather than papered
  over. See D-1.
- **affected files:** to be decided with D-1.

---

# Decisions

## D-1 · Close the bay gap at the authoritative table, not in the agent

**Context:** F-5. The bay pipeline reads `platform.bay_configuration` (SQL),
projected into Neo4j as `Warehouse` and `Bay`. It contains only the six bootstrap
rows a migration seeded for `WH-CHENNAI-01`. Every seeded order names a different
warehouse.

**Options considered**

| Option | Verdict |
|---|---|
| Accept the unresolved result and render it truthfully | Satisfies the letter of Phase 7, but makes the normal path permanently degenerate: the flow could never once show a real bay, and adversarial case 14 ("bay configuration references an unavailable warehouse") would be indistinguishable from the happy path. |
| Special-case the seeded warehouses in the agent or the workflow | Forbidden outright -- hardcoded bay mappings, and a test-shaped branch inside a business agent. |
| Point the agent at `warehouseMaster` in source Mongo | Changes the authority for bays to a collection the generator itself calls provisional, and bypasses the descriptor the graph projection is compiled from. |
| **Materialise the seeded warehouses' bays into `platform.bay_configuration`, then let the existing projection carry them into the graph** | **Chosen.** One authority, unchanged contract, unchanged agent. The seed generator already invents the warehouses and their bays; the defect is that nothing lands them in the table the platform actually reads. |

**Constraints on the implementation**

- Additive and idempotent. The six bootstrap `WH-CHENNAI-01` rows are left exactly
  as they are; re-running must not double-insert.
- No literal bay list in code. Every row is derived from the seeded
  `warehouseMaster` document -- its `bays`, `capacityUnits`, `acceptsHazmat` and
  `acceptsOversize` -- and from the configured shipping paths and product types,
  never from a hand-typed table.
- The graph projection for the bay asset is re-run afterwards, so SQL and Neo4j
  agree; a bay that exists in only one of them is the same defect in a new place.
- Adversarial case 14 keeps a real subject: at least one seeded warehouse is left
  without bays on purpose, so "no bay is configured for this warehouse" stays a
  reachable, tested outcome rather than dead code.

---

## F-6 · The support draft is the literal "please create an RMA" message

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `workflows/return_case_activities.py::draft_support_request`, the
  fallback return value:

  ```
  f"Hello -- we have a return to raise against {order}. "
  "Could you create the RMA and send the return label or pickup "
  f"instructions when you have a moment? {_branch_associate_sentence(plain)}"
  "Happy to supply anything else you need. Thank you."
  ```

- **finding:** this is what Support receives. `self._drafter` is the model path
  and `backend/scripts/run_return_workflow_worker.py` does not pass one, so the
  fallback **is** the production text on this deployment. It names one order
  reference and nothing else -- no customer, no product, no colour, no quantity,
  no return detail, no bay. Phase 8 forbids exactly this message.
- **decision:** compose the Support request deterministically from projected case
  state against the frozen template, and persist the same values structurally on
  the work item so the UI never parses the prose back into fields. No model
  drafts it: a generated draft cannot be held to "do not invent unavailable
  values", which is the rule that matters most in a handoff a human acts on.
- **affected files:** `workflows/return_case_activities.py` and the support
  work-item creation path.

---

## F-7 · Durable interception could hold a reasoning turn but never resume one

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `ai/gateway/interception_policy.py::interception_id_for` built the
  id from `correlation.correlation_id`, and
  `dynamic_knowledge/api/order_agent.py:335` sets that from
  `_meta(request).request_id` -- the id the correlation middleware mints **per
  HTTP request**. Measured: three attempts at one turn produced
  `aiq-890865ca…`, `aiq-901cba3f…`, `aiq-7243b17e…`.
- **finding:** the function's own docstring states the invariant it was
  violating -- *"two turns of the same conversation are distinct, and one turn
  retried is not."* An operator answered a held request; the retry derived a
  different id, opened a second interception, and asked the same question again.
  Forever.
- **decision:** add `InvocationCorrelation.turn_id`, populate it from
  `AgentTurnContext.client_turn_id` (the idempotency key the workflow already
  dedupes on), and prefer it over `correlation_id` in `interception_id_for`.
  Callers with no turn identity keep the old behaviour exactly.
- **affected files:** `ai/gateway/telemetry.py`,
  `ai/gateway/interception_policy.py`,
  `dynamic_knowledge/integration/model_gateway.py`.

---

## F-8 · A retried turn re-read the clock, so it asked a different question

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `dynamic_knowledge/order_agent/coordinator.py`, `run_turn`:
  `as_of = datetime.now(UTC)` on every invocation. Two attempts at one turn
  carried `as_of` `09:06:08.487812Z` and `09:06:35.570231Z`.
- **finding:** `as_of` travels in `contextJson` and in the temporal-grounding
  prompt, so it is part of the request digest. Beyond breaking interception, a
  retried turn genuinely reasons against a different instant -- every relative
  date window shifts -- which contradicts `temporal_grounding.py`'s own stated
  design (*"The clock is read once"*) and the `ORDER_AGENT_TURN_NOT_GROUNDED`
  guard that exists to enforce it.
- **decision:** `ReasoningRunLifecycle.start_run` already writes `created_at` for
  the attempt and is already idempotent per thread. Return it, and pin `as_of` to
  it. One durable clock read per attempt, by construction.
- **second defect found while fixing it:** BSON stores milliseconds, so the
  first caller held microseconds and the retry read back a truncated value --
  identical to the eye, different to a hash. `start_run` now truncates before
  storing and returns the truncated instant.
- **affected files:** `platform/reasoning/run_lifecycle.py`,
  `dynamic_knowledge/order_agent/coordinator.py`.

---

## F-9 · An answered interception's response was built and then thrown away

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `ai/gateway/final_dispatch.py::dispatch` returned a
  `DispatchOutcome` carrying `_human_response(...)` but never ran `validate`, so
  `value` was `None`; `ai/gateway/structured_invocation.py` then raised on
  `outcome.decision is not ALLOW_PROVIDER` regardless. Observed:
  `StandardReasoningUnavailable: Order Agent was not dispatched: HUMAN_RESPONSE
  (ANSWERED)` -- the answer was found and refused in the same breath.
- **finding:** with F-7 and F-8 fixed the operator's answer reached the
  dispatcher, and the dispatcher discarded it. Interception was write-only.
- **decision:** put the human answer through **the same two gates a model answer
  goes through, in the same order** -- `inspect_output`, then the caller's
  `validate` -- and return an outcome with a value; record the attempt so an
  operator-answered request is visible in the trace. The invoker accepts
  `HUMAN_RESPONSE` only when it carries a value, and reports provider `MANUAL`
  so human text is never counted as model output. This is the rule the response
  review point already states for edits; requests now follow it too.
- **affected files:** `ai/gateway/final_dispatch.py`,
  `ai/gateway/structured_invocation.py`.

---

## F-10 · Interception cannot drive a multi-step turn, whatever is fixed

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** with F-7/F-8/F-9 fixed, the first held request of a turn resumed
  correctly and the loop advanced to `ORDER_AGENT_REASONING_COMPLETING_V1`. The
  retry that followed re-executed the whole turn from its first node: the order
  search ran again and minted a new `query_execution_id`
  (`09aff1cc…` -> `b9a6c688…`) and a new `candidate_set_id`, so the second step's
  digest differed and a *third* interception opened.
- **finding:** a turn is a graph run, and a failed run is replayed rather than
  resumed. Only the first reasoning step of a turn can ever be recognised on the
  way back in, because every later step's context contains identifiers minted by
  the steps before it. This is structural, not a bug in the fixes above.
- **decision:** superseded by **D-2**. Governance interception (hold / allow /
  cancel, failing fast) is not the mechanism for hand-driving a conversation, and
  the repository already ships the one that is.
- **affected files:** none. The three fixes above stand on their own merits.

---

## D-2 · Manual LLM mode for this validation is the MANUAL provider, not interception

**Context:** F-10. The repository has two manual mechanisms and they are not
interchangeable, which `final_dispatch.py`'s own docstring says plainly:

> **`DurableInterceptionProvider` is not this.** It is a MANUAL provider, gated to
> development and test, that *replaces* the model with a human... Gating dispatch
> and substituting the thing dispatched to are different mechanisms.

| Mechanism | What it does | Fit |
|---|---|---|
| Interception policy (`interceptMode`) | Holds a dispatch and **fails the caller fast**, to be retried after a decision | An operator control. Cannot carry a multi-step turn (F-10). |
| MANUAL provider, `ai_manual_handoff=UI` (`DurableInterceptionProvider`) | Is selected as a **route**, opens the same held request, and **waits** up to 600s for the answer | The reasoning loop never fails, never replays, and every step of a turn is answered in order. |

**Decision.** Run this validation with `PLATFORM_AI_PROVIDER_ORDER=MANUAL` and
`PLATFORM_AI_MANUAL_HANDOFF=UI`, and `interceptMode` **off**. The held requests
appear in the AI Control Center exactly as before -- the same store, the same
`/api/ai/interceptions` endpoints -- so manual mode is still answered in the UI.

Supporting evidence that this is the intended pairing: every
`ORDER_AGENT_REASONING_*_V1` task in `config/ai_gateway.yaml` already lists
`MANUAL` in `allowedProviders`, and `.env.example` documents the provider-order
and handoff settings together.

`.env` is backed up to `.env.backup-basic-flow` before the change. Nothing in
`config/returns/production.yaml` is touched.

---

## F-11 · The platform knew the customer and recorded nothing about them

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `case_projection/assembly.py::project_customer` reads two facts,
  `customer_name` (`assembly.py:197`) and `customer_id` (`:196`). Their only
  writer was the reasoning model's `observed_facts`. `redact_payload` masks
  `customer_name`, `ship_to_name` and `ship_to_phone` before a candidate row
  reaches a prompt -- measured on the live search result for `CQ800002`, which
  arrives at the model as `"customer_name": "[REDACTED]"`.
- **finding:** the model is structurally forbidden to see the customer, so it can
  never report one, so the facts are never written and **every confirmed case
  projects `customer: null`** -- verified on case `e5ce5c59`. The Support handoff
  is then about a customer it cannot name.
- **decision:** the workflow reads the identity off the confirmed order through
  the paths the release already binds (`source_resolution.customer_name_paths`,
  `customer_id_paths`) and records it as `SYSTEM` / `OBSERVED`. Not
  `CHANNEL_A` / `STATED`: no associate said it, and the difference is what a
  reader needs on the day a source read and a person disagree.
- **affected files:** `workflows/case_customer_identity.py` (new),
  `workflows/return_case_activities.py`, `workflows/return_case_workflow.py`,
  `workflows/worker.py`.
- **validated:** V-11.

---

## F-12 · Colour is not on the order line and is not in the schema

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `GET /api/cases/{id}/order-lines` returned
  `description: "6X12 CEIL ALUM 4-WAY REG SAND"` and no colour. The `product`
  entity in `config/dynamic_knowledge/active-schema.return-order.yaml` declares
  fourteen fields and **none is a colour**. The value does exist at the source:
  `lkpSearchProduct._id = "4000096"` carries `eco.colorFinish: ["Sandtone"]`,
  and 253 of 1000 catalogue documents carry one.
- **finding:** `production.yaml` already declares `product_colour` as a
  return-detail field with a note saying it is unusable until a colour is
  resolvable. It was unusable because nothing bound a path to it.
- **decision:** bind it in `source_resolution.product_colour_paths` and resolve
  it with one `$in` per order. **Never inferred from the description** --
  `"16X20 STL W/ FLTR FRAME RTN AIR GRL WHIT"` is a description that ends in a
  colour word, not a statement of colour.
- **affected files:** `configuration/return_configuration.py`,
  `config/returns/production.yaml`,
  `operations/order_lines/product_attributes.py` (new),
  `operations/order_lines/case_detail.py` (new), `api/order_lines.py`.
- **validated:** V-12.

---

## F-13 · A key added inside an existing config block cannot reach a live release

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `product_colour_paths` was added to `production.yaml` and
  `bootstrap_graph_configuration.py` was re-run; the runtime configuration still
  reported `"product_colour_paths": []`.
- **finding:** not new -- the codebase already records it. The
  `SelectionVocabularyConfiguration` docstring names it as **platform defect
  D11**: `bootstrap_graph_configuration` merges the packaged file *underneath* an
  active release at **top-level granularity**, so a key added inside an existing
  block is dropped by that block's released value and can never arrive. It is why
  `return_eligibility_policy`, `copilot` and `selection_vocabulary` are all
  top-level.
- **decision:** publish it through the release API instead, which is the
  established path for a configuration change and is RFC 7396, so naming the one
  key is enough. `production.yaml` keeps the binding for a deployment bootstrapped
  from scratch. **Not worked around in code** -- a code-side default would make
  the release the platform is running and the file it was compiled from disagree.
- **affected files:** none. Recorded because it explains why a correct
  configuration edit appeared to do nothing.

---

## F-14 · Support was asked about a case id, not about a return

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** the message actually delivered to the Support thread of case
  `721fb62e`, read back from `/api/v1/return-support/work-items/{id}/messages`:

  ```
  Hello -- we have a return to raise against CQ800002. Could you create the RMA
  and send the return label or pickup instructions when you have a moment?
  Happy to supply anything else you need. Thank you.
  ```

  `businessPayload` was `{"caseId": "721fb62e-..."}`.
- **finding:** F-6 confirmed in production behaviour, not only in the source.
  Two separate defects in one message: no business fact a human could act on, and
  nothing structured for a screen to read.
- **decision:** `operations/support_handoff.py` composes both halves from
  authoritative case state; `open_case_thread` persists the structured half on
  the opening message beside the prose.
- **affected files:** `operations/support_handoff.py` (new),
  `workflows/return_case_activities.py`, `workflows/return_case_workflow.py`,
  `operations/return_support/service.py`.
- **validated:** V-13.

---

## F-15 · The handoff went before anyone had described the return

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `ReturnCaseWorkflow.run` runs `_gather_bay` -> `_policy_cleared`
  -> `_open_support` with nothing between the gate and the handoff. Observed on
  case `721fb62e`: the work item opened within a second of confirmation, while
  `awaiting` still reported `RETURN_METHOD` and `selectedItems` was `null`.
- **finding:** Support received a request naming an order and no line, no
  quantity and no reason -- a task a human cannot act on and has to come back and
  ask about. Phase 5 requires the opposite: *"prevent final support handoff while
  required information remains missing."*
- **decision:** a configured precondition, not an unconditional change of
  behaviour. `return_case.return_details_required` defaults to **false**, which is
  exactly what the platform did before; turning it on makes the case wait
  `return_details_wait_seconds` for a selection and park if none arrives. Turning
  it on changes when a human is contacted, which is an operator's decision.
  The wait is ended by a `return_details_recorded` signal sent by the write that
  records the selection -- event-driven, so a counter conversation feels no delay.
- **affected files:** `configuration/return_configuration.py`,
  `workflows/return_case_workflow.py`, `workflows/return_case_launcher.py`,
  `api/order_lines.py`.
- **validated:** V-13.

---

## D-3 · Known baseline failures for this task

Two, both measured with this task's changes **stashed**, so neither is
attributable to it:

| Failure | Evidence |
|---|---|
| `ruff I001` on `tests/dynamic_knowledge/test_a_turn_that_asks_is_not_complete.py:26` | Pre-dates the baseline commit. `ruff --fix` corrected it once as a side effect and the file was **reverted**: an unrelated import sort does not belong in this change. |
| 5 ESLint errors -- `canonicalHandlers.contract.test.ts:64`, `schemaConformance.ts:71,77,254` | Identical output with the working tree stashed. |

A gate reporting exactly these is a pass. Anything more is a regression.

---

## F-16 · A pasted order number found nothing

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** adversarial case 2, driven through the live agent.
  `"  CQ800002  "` searched verbatim and returned `total_found: 0`; the reasoning
  loop routed to `ORDER_AGENT_REASONING_UNRESOLVED_V1`. The same number without
  padding returned exactly one order.
- **finding:** `IdentificationField.values_from` never trimmed. `OrderSearchIntent`'s
  own docstring says values "are read through `IdentificationCatalogue.parse`,
  which applies the configured multiplicity, **normalization** and validation" --
  normalisation was claimed and not performed. An associate pasting an order
  number out of an email is told there is no such order.
- **decision:** strip surrounding whitespace before validation and before the
  search, on strings only. Not a normalisation policy: the repository already
  holds the principle in `ReturnContactRequest._trimmed` -- *"surrounding
  whitespace is typing, not content"*. A value that is only whitespace is dropped
  like `""` already was; a non-string is untouched, because coercing one to text
  in order to strip it would change what was supplied.
- **affected files:** `dynamic_knowledge/order_agent/identification.py`
- **validated:** re-driven live -- `total_found: 1`, candidate `CQ800002`, stage
  `COMPLETING`. Three regression tests in
  `tests/dynamic_knowledge/test_identification_catalogue_extensibility.py`.

---

## F-17 · Eight tests pinned the message this work replaced

- **timestamp:** 2026-08-21
- **baseline commit:** `47f5abd`
- **evidence:** `tests/test_support_draft_carries_the_branch_associate.py`
  asserted the old prose verbatim, including
  `assert drafted == "Hello -- we have a return to raise against CQ363350. ..."`.
- **finding:** the tests' *intent* was entirely sound and still is -- the branch
  contact must reach Support, nothing may be filled in to round a sentence off,
  a retracted contact reads as absent. Only the **form** was obsolete.
- **decision:** rewritten onto the composed request with every assertion of
  intent kept, plus two new ones the old shape could not express: an absent
  contact is now *stated* rather than omitted, and a drafter that fails changes
  nothing. Nothing was deleted to make a test pass.
- **behaviour deliberately changed, and recorded here rather than buried:** a
  configured `drafter` used to **replace** the whole message. It now writes a
  labelled note *under* the structured request. A generated draft cannot be held
  to "do not invent unavailable values", and a reader has to be able to tell the
  composed facts from a written note. The activity's docstring initially claimed
  this before the code did; both now agree.
- **affected files:** `tests/test_support_draft_carries_the_branch_associate.py`,
  `workflows/return_case_activities.py`

---

## F-18 · The request said its status was the one it was about to leave

- **timestamp:** 2026-08-21
- **found by:** reading the Support console in the browser
- **evidence:** the delivered message read `Current Workflow Status:
  GATHERING_INFO` while the case pane beside it read `AWAITING_SUPPORT`.
- **finding:** mine, introduced with the composed request. The draft is composed
  *before* the thread is opened and before `_set_status(AWAITING_SUPPORT)`, so a
  field labelled "current" always disagrees with the live status shown next to
  it -- and a reader comparing the two would be right to distrust one of them.
- **decision:** every field in the message is a snapshot; this is the one where
  saying so matters. Relabelled `Workflow Status at Handoff`, and the payload key
  to `workflowStatusAtHandoff`. Not changed to the status it is *about* to have,
  which would be a guess about the future dressed as a reading.
- **affected files:** `operations/support_handoff.py`

---

## F-19 · A suspended policy gate was reported to the associate as pending

- **timestamp:** 2026-08-21
- **found by:** reading the Copilot's evaluation pane in the browser
- **evidence:** the pane showed `Policy Evaluation Pending` under
  `Authoritative Policy Engine`, with `Applied Policy Code: Pending` and
  `Restocking Fee: Pending`, on a case whose gate had been suspended by
  configuration.
- **finding:** `ReturnEvaluationMode` takes only `PolicyEvaluationProjection`,
  and a suspended gate produces none -- which is exactly what a case that has
  not been evaluated *yet* looks like. Reading the second as the first tells an
  associate a verdict is on its way when none is coming. The Support handoff got
  this right; the associate's own screen did not.
- **decision:** pass `policy_evaluation_state` and the operator's reason from the
  case fact log -- `projectedFactString` is the established way this page reads a
  projected fact -- and render `Policy Evaluation Skipped`, badge `SKIPPED`,
  subtitle `No policy was applied to this return`, with the reason beneath it and
  `Not evaluated` in place of `Pending`. A real evaluation always wins over the
  state fact, so a stale fact cannot displace a decision.
- **caught by the repository's own guard:** the first attempt wrote
  `policySkipReason ?? "Suspended by configuration"`, and
  `ReturnCopilotFabrication.test.ts` refused it -- a literal fallback on a
  business value is a reason nobody gave. The reason now renders only when it
  exists.
- **affected files:** `domains/returns/modes/ReturnEvaluationMode.tsx`,
  `domains/returns/ReturnCopilotPage.tsx`

---

## F-20 · The verified-facts panel could not show the return details it promised

- **timestamp:** 2026-08-21
- **found by:** the operator asking where the quantity, reason and branch details
  had gone
- **evidence:** the panel showed order number, customer name and quantity. Its
  own empty state promises *"SKU, quantity, colour or finish, **reason** and
  order number"*. The case carried `branch_associate_name`,
  `branch_associate_email` and `branch_associate_phone` as facts, and
  `selectedItems[0]` carried `reason: ORDERED_IN_ERROR` and
  `condition: NEW_IN_ORIGINAL_PACKAGING`.
- **finding:** three separate causes, and only the quantity had been solved:
  1. **Return reason** is a configured fact (`clarification_policy` ranks it last
     of eighteen), so the panel *asked* for it -- and looked for a case fact of
     that name, which nothing writes. `POST /selected-items` records it against
     the return **item**.
  2. **Product condition** is on the item too and is named by no configured
     field, so nothing asked for it at all.
  3. **The branch associate's three contact facts** are on the case fact log, but
     no configured field names them either, so the panel's
     configured-plus-captured union never reached them. The existing "Branch" row
     is a different thing entirely -- the principal's branch id.
  A contact collected because a carrier needs it reached a database and not a
  screen, which is the same failure `test_support_draft_carries_the_branch_associate`
  exists to prevent on the Support side.
- **decision:** read reason and condition from the selection the way quantity
  already is, bounded the same way. Reason keeps its configured rank whichever
  source supplied it and prefers the recorded copy over the spoken one, so a case
  holding both shows one row. Condition and the three contact rows take the
  documented alphabetical tail, because no configured field names them.
- **affected files:** `domains/returns/extractedFields.ts`
- **validated:** live -- the panel now reads Order number, Customer name, Return
  reason, Branch associate, Branch associate email, Branch associate phone,
  Product condition, Quantity.

---

## F-21 · Reopening a conversation that ended on a question shows no question

- **timestamp:** 2026-08-21
- **found by:** the operator's own run in the browser, conversation
  `disc-514a5ef4-062f-42b7-bacc-8c41827d88ec`
- **evidence:** the turn completed -- Temporal reports
  `WorkflowExecutionUpdateCompleted` -- and the live pane rendered the agent's
  three statements. `GET /api/v2/order-agent/conversations/{id}/transcript` then
  returned **one** message, the associate's. Reloading the page lost the agent's
  entire reply, including the pending question. Measured across three
  conversations:

  | Conversation | Last action | Agent message in transcript |
  |---|---|---|
  | `6d6822ef` turn 1 | `RESPOND` | yes |
  | `6d6822ef` turn 2 | `CLARIFY` | **no** |
  | `b9f9c66d` turn 2 | `CLARIFY` | **no** |
  | `disc-514a5ef4` turn 1 | `CLARIFY` | **no** |

- **finding:** two individually-reasonable decisions that are wrong together.

  `coordinator._extended_transcript` is called with `response=None` on the paused
  branch, and says why: *"A turn that produced no response (a pause awaiting a
  clarification answer) still records what the associate said: the question the
  agent asked is already carried in `clarification_exchanges`."* True, and right
  for the **agent**, which reads that field.

  `api/order_agent.read_conversation_transcript` then serves that same store to a
  **human**: *"What was said, so reopening a conversation shows it rather than a
  blank pane. Same bounded transcript the agent itself reasons over."*

  So the store deliberately omits the question, and the endpoint deliberately
  serves the store -- and the endpoint's own first sentence describes the failure
  this produces. One store, two consumers, and only one of them was considered.

- **why it matters:** a narrowing conversation ends on a question nearly every
  turn. An associate who refreshes, or comes back after helping someone else,
  sees their own words and nothing else -- and the question they were being asked
  is invisible while the workflow is still waiting for its answer.
- **decision:** deferred to the operator, mid-test. The fix belongs in the
  **endpoint**, not the store: merge `clarification_exchanges` into what is
  served to a human, leaving what the agent reasons over exactly as it is. The
  alternative -- appending the question to the stored transcript -- would put it
  in front of the model twice, and the packaged prompt already warns the
  transcript is not a fact log.
- **affected files (proposed):** `dynamic_knowledge/api/order_agent.py`, and
  whatever `read_transcript` resolves to on the conversation repository.

---

## F-22 · The discriminator ranker had no measurement, so it recommended the worst question

- **timestamp:** 2026-08-21
- **found by:** the same run -- *"find order for dane and the product he received
  is damaged"*
- **evidence:** the search resolved **seven** customers, five returned, each a
  distinct `customer_id` on a different branch account (NASH, GARDEN x2, DALLAS,
  LAKEWOOD). Every entry in `contextJson.suggested_discriminators` came back
  `"basis": "CONFIGURED_PRIORITY"`, `"reason": "not profiled; ranked by the
  configured question order"`, with no `distinctValuesAmongCandidates` on any of
  them. The top recommendation was **order number** at 0.95, then order id, then
  email, then phone.
- **finding:** measured against the rows, `account_id` splits the five candidates
  four ways and is the only field that splits them at all -- `ship_to_city`,
  `ship_to_postal_code`, `order_status` and `shipping_method` are all null at the
  customer-resolution stage, and `customer_name` arrives `[REDACTED]`. The ranker
  offered none of that and fell back to the operator's question order, whose top
  entry is the one thing an associate with the customer in front of them does not
  have. This is the complaint recorded against the earlier half-name work,
  reproduced on a different name.
- **not fixed during the run:** the packaged prompt is explicit that the ranking
  is *evidence, not an instruction* -- *"prefer what the data says, and use the
  configured order only to break a tie the data cannot"* -- so a model that reads
  its own instructions recovers, as this turn did by asking for the branch. The
  defect is that recovery is left to the model rather than to the ranker.
- **decision:** deferred. Changing ranking behaviour underneath a live
  conversation would make the operator's next turn behave differently from their
  last one for no reason they could see.

---

## F-22a · Superseding F-22: the ranker was never given anything, and then scored it backwards

F-22 recorded the symptom -- every field `CONFIGURED_PRIORITY / not profiled`,
order number on top. Fixing it turned up **two** causes, and the second is worse
than the first.

### Cause 1 · The candidates never reached the ranker

`order_search` writes **two** evidence records from one search:

| record | contents | where its id goes |
|---|---|---|
| `full_evidence` | every candidate | `orderSearchCache.evidenceRef`, and the `CandidateSet` |
| `page_evidence` | the page shown | **`state["evidence_refs"]`** -- the only one the turn rehydrates |

`_next_discriminators` scanned the turn's evidence for `evidenceRef` -- the id of
the record the turn never carries. Measured on the operator's own conversation:
`evidenceRef = 0740e3bc-…`, turn evidence `b537115a-…`. The loop matched nothing,
`candidates` stayed empty, `narrowing` was false, and no ranking ever considered
the candidates. **For every conversation, always** -- which is why the earlier
half-name work on `rank_discriminators` did not change what the associate saw.

`rank_discriminators` has eleven tests, several asserting that a field the
candidates disagree on outranks one they share. Every one passes candidates in
directly. `_next_discriminators`, which finds them, had **no test at all**.

**Fixed:** the cache carries `pageEvidenceRef` and the lookup prefers it,
falling back to `evidenceRef` for a cache written before the field existed.

### Cause 2 · Splitting the candidates *lowered* a field's score

With the candidates finally arriving, the ranking still did not change -- because
the narrowing arithmetic was inverted:

```
score = min(1.0, score * (distinct / len(candidates)) + score * 0.25)
```

That multiplier is **below 1 for anything short of a perfect split**, while a
field no candidate carries kept its configured score untouched. Measured against
four candidates sharing a state and differing in city:

| field | score | what it knows |
|---|---|---|
| `orderNumbers` | **0.950** | nothing about these candidates |
| `orderIds` | 0.934 | nothing |
| ...nine more... | | nothing |
| `cities` | **0.338** | **splits them 2 ways -- the only field that splits them at all** |
| `states` | 0.000 | every candidate agrees |

The one useful question ranked **twelfth**, behind eleven fields carrying no
information whatsoever.

**Fixed:** `_narrowing_score` places a measured splitter above `_UNKNOWN_CEILING`
(0.5), ordered within that band mostly by how finely it cuts and partly by the
configured priority; a field the candidates are silent about is capped at the
ceiling. Same case now ranks `cities` first at 0.690, unknowns tied at 0.500 in
configured order, `states` at 0.

All sixteen existing planner tests still pass -- the relationships they encode
were right, and nothing had ever tested the two this missed.

### Still open · the branch is not a searchable signal

The packaged prompt tells the model that *"the identifying fields are the branch
or account the order sits on, the full business name, and the contact details"*.
`discovery.identification_fields` publishes seventeen signals and **none of them
is a branch or account**. At the customer-resolution stage the candidate rows
carry only `customer_id` and `account_id`, and no catalogue field searches
either -- so on that stage's results the ranker still has nothing measurable,
and now says so honestly (`no candidate carries this field`) rather than
recommending the order number with false confidence.

Closing that needs a new configured identification field bound to the account,
which is an operator decision about what the agent may search, not a code fix.
Recorded rather than done.

---

## F-21a · The paused-turn transcript, fixed

The paused branch called `_extended_transcript(..., response=None)`. It now
passes the question, which the branch already holds as `question` and already
passes to the turn result. One argument.

The rejected alternative was merging `clarification_exchanges` into the endpoint:
that field is written from the value `interrupt()` *returns*, so while the
question is pending it is empty -- it could never have recovered a question that
had not been answered yet.

---

## F-23 · Reopening any earlier conversation showed only the associate's own words

F-21a fixed the *writer*: a turn that pauses on a question now records the
question. That does nothing for the conversations already in the store, and the
history list is exactly what the operator was reaching for -- so the report was
still "history not loaded".

The list itself was never broken: `/api/v2/order-agent/conversations` returned
thirty rows, and the panel opened them. What came back was one message.

Measured on the operator's own conversation, in Mongo:

```
=== disc-9a4d25f9-... "find order for BOYLE" ===
  state.transcript: 1 entries
    [associate] find order for BOYLE
  turns: 1
    ui-disc-9a4d25f9-...  pending=True statements=1
      -> Which order are you looking for? If you have the order number, ...
```

**Nothing was lost -- it was mis-read.** A conversation document keeps two
records of the same turn:

| record | written by | contains |
|---|---|---|
| `state.transcript` | the graph, per turn | what the endpoint serves; missed the question on a paused turn |
| `turns[key].result` | `commit_turn`, per turn | the whole `AgentTurnResult` -- and on a paused turn the `response` **is** the question |

`_transcript_of` read only the first. It now rebuilds: the associate's side from
the stored transcript, each reply from `turns`, ordered by the
`conversation_version` each result carries rather than by insertion order --
`turns` is keyed by idempotency key, which carries no ordering.

Zipping by position is safe because a conversation strictly alternates, one
associate message to one reply. **When the counts disagree the stored transcript
is served unchanged**: a transcript truncated to its limit while `turns` kept
every turn cannot be aligned, and a plausible-looking wrong order is worse than
a short one.

### Why not `clarification_exchanges`

It is written from the value `interrupt()` *returns*. While a question is
pending it holds nothing, so it could never recover an unanswered question --
which is the only case that was broken.

### Verified live

Resuming `disc-514a5ef4-...` in the Copilot after the fix renders both roles:
the associate's `find order for dane and the product he received is damaged`,
then the agent's `Seven customers match Dane ... Which branch is this Dane on
-- NASH, GARDEN, DALLAS or LAKEWOOD?`. Before the fix the same row rendered the
associate's line alone, with progress reset to `Ready`, while the workflow was
still waiting for the answer.

---

## F-24 · Candidates are live-turn state, and a resumed conversation drops them

Reported as "candidates are not populated". They populate on a live turn, at
both stages, measured on a driven run:

| Stage | What the Context pane drew |
|---|---|
| Customer resolution | `Candidates (5)`, *Showing 5 of 7 matched*, Account Id / Customer Name, Select per row |
| Order lines | *Showing 10 of 25 matched*, Sales Order Number / Line Number / SKU / Product Description / Ordered Quantity, "Show 10 more · 15 not shown" |

They are dropped on **re-entry**. `readTranscript` returns messages and nothing
else, and `ReturnCopilotPage.open` clears the list on purpose -- for a resumed
*case* that is right, because the confirmed order is rebuilt from the case and a
stale search would sit beside it. For a resumed *past search* that raised no
case, the list is simply gone.

Nothing is lost server-side: `conversation_state.orderSearchCache.candidateSet`
holds the ids and the query execution id for thirty minutes, and the evidence
record still holds the rows. Restoring them needs the transcript endpoint to
carry that cache. **Recorded, not done** -- it is an API shape change and the
three defects below were the ones blocking a person reading a screen.

---

## F-25 · The progress rail drew an internal customer id

`ProgressTruthPane` read `customerReference ?? displayName`, so a case that knew
the customer was `DUANE HOPKINS` drew `600654` -- on the rail an associate reads
*while talking to that customer*. The Order Agent's own prompt forbids this in as
many words (`Never ask for or show a customer_id`); the screen beside it did it
anyway.

The fallback was backwards. It now reads `displayName ?? customerReference`: the
reference stays for a case that has resolved an id and not yet a name, which is
something true to show.

A test asserted `CUST-9012` on that chip, so the defect was pinned. Updated
rather than deleted -- its intent, *the rail names the customer*, is right.

---

## F-26 · Support was told to wait for a policy that had been switched off

The console printed **"Waiting on POLICY, RETURN_METHOD."** two inches below
**"Policy Evaluation: Skipped by configuration"**, on the same screen.

`AwaitingDimension.POLICY` means *no APPROVE on the effective decision yet*.
With `policy_evaluation.enabled = false` there will never be one: the gate
deliberately writes no evaluation, no route and no decision, because each would
be an answer it did not produce. So `route_authority_stands` read the missing
approval as "not yet" and the case awaited `POLICY` for the rest of its life --
and `businessComplete` was unreachable however fully the return was fulfilled.

The suspension reached the workflow (`PolicyGateState.SKIPPED_BY_CONFIGURATION`)
and the Evaluation pane, and stopped there. It now reaches the completion
profile too, read from the fact the gate writes -- which is the only place it
exists, and the only thing that distinguishes *switched off* from *has not run
yet*, both of which carry `policyEvaluation: null`.

**It is not an approval and is not recorded as one.** No decision is
manufactured, `POLICY_APPROVED` is still never set, and the message still says
skipped and quotes the operator's reason.

`PolicyGateState` lives in the workflow module, which the projection may not
import without inverting the dependency and pulling `temporalio` into every
reader of a case, so the two strings are declared on both sides -- pinned
together by a test that imports the enum and asserts they match.

---

## F-27 · Every row in the Support queue read `Return request for case <uuid>`

Thirteen open requests, thirteen indistinguishable lines. A human had to open
each one to find out what it was about -- while the order number, the product
and the customer were all in the message underneath. F-17 fixed the body and
left the subject.

`compose_support_handoff` now yields a third output beside `text` and `payload`:
a `subject` built from the same facts under the same rule -- nothing absent is
invented, so a return that knows only its order number gets a shorter subject
rather than a padded one, and a case that knows nothing falls back to the case
id, because a row with no identity is worse than one identified by a uuid.

    Return CQ800002 line 1 · 6X12 CEIL ALUM 4-WAY REG SAND · THELMA OSBORNE

Written once when the thread opens, so rows raised before this keep the old
wording. That is not a migration and is not treated as one.

### The duplication this turned up

`SupportRequestDraft` is declared **twice** -- once in `return_case_workflow` and
once in `return_case_activities`, because the workflow sandbox may not import the
activity module. Adding `subject` to one side failed thirty tests with
`unexpected keyword argument 'subject'`, which is the good outcome; the bad one
is a field accepted on both sides that carries nothing across the boundary.
Nothing was checking the pair. A test now asserts the two shapes match.
