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
