# Implementation plan — execution status

Against [`FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md`](FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md).
Last verified 2026-08-12 on `refactor/unified-return-platform`.

**A step is "done" only when its own Validation clause holds.** Two steps below were previously
reported complete and are recorded here as partial, because theirs does not.

## Wave 0 — complete

| Step | Status |
|---|---|
| W0.1 Rotate AI credentials | **done** — keys rotated; `_reject_inline_ai_credentials` refuses inline keys outside development/test; duplicate `backend/.env` removed |
| W0.2 Mount `/api/agents` | done — verified against a running backend, not MSW |
| W0.3 Tenant and principal isolation | done — scope in the query filter; cross-tenant and guessed-id both covered |
| W0.4 Serialized PII escape | done — redaction at both `ProviderRequest` sites, recursing into `contextJson` |
| W0.5 Anthropic and OpenAI contracts | done — schema as a forced tool; `max_output_tokens` honoured |
| W0.6 Freeze the duplicate runtime | done — exact importer set pinned; additions *and* removals fail |
| W0.7 Discovery smoke net | done — 19 scenarios; verified by injecting a regression |

## Wave 1 — complete

W1.1 case/facts/return records · W1.2 `CONFIRM_ORDER` · W1.3 multi-return · W1.4
`ReturnCaseWorkflow` (proven by a real worker restart mid-wait, per the step's explicit refusal
of unit-test evidence) · W1.5 Support console · W1.6 outcome into Channel A · W1.7 bay, failure
policy, timings · W1.8 case list and resume.

## Wave 2 — partial

| Step | Status |
|---|---|
| W2.1 Analyzer → runtime schema | **done** — approved draft compiles to an `ActiveSchema` release; runtime prefers it over YAML |
| W2.2 Split shape from source binding | **partial** — binding catalogue, store and API shipped; `DOMAIN_SOURCE_COLLECTIONS` still has 3 references in `operations/repository.py`, so the Validation clause (rename `salesInv → salesInvV2` through configuration only) would fail |
| W2.3 Re-analysis and migration | done — three-way diff, proposals as typed mutations, migration plan recorded before the pointer moves |
| W2.4 Return and warehouse entities | **partial** — return entities added, but **by hand-editing the descriptor**, which the step forbids; **no warehouse or bay entity exists**. Blocked on the MSSQL analyzer connector (W4.5), as the step's own Failure condition predicts |
| W2.5 Return on-demand sync | **not started** — nothing in `return_case_activities.py` requests a record-scoped sync |
| W2.6 Fulfillment on-demand sync | **not started** — the mechanism works and was fixed (see below), but `agents/fulfillment.py` and `workflows/fulfillment_tracking.py` still do not call it |
| W2.7 Warehouse and bay on-demand sync | **not started** — blocked on W2.4's warehouse entity |
| W2.8 Sync control (S6) and incremental sync | **partial** — S6 ships with run list, filters, detail and manual trigger; `incremental_sync` not confirmed implemented against the cursor contract |

### A defect found and fixed inside W2.5/W2.6's area

The on-demand sync path was complete and wired end to end — guard, planner, connector,
extractor, projector, writer — and **could not put the anchored order into the graph**. The read
projection was built from the anchoring entity's own mapped fields, so it omitted the
discriminator that entity's `where` selector tests, and everything under the exploded line
array. The order the sync was requested for was the one entity never projected; the receipt read
`SUCCEEDED`, and the agent told the associate it had checked the source system directly.

Projection is now derived from every mapped field of every entity bound to the source, plus the
paths its selectors test.

## Gate A — not run

The 19 steps are in the plan. Nothing is deleted before it, so Wave 3 is blocked.

## Wave 3 — blocked on Gate A

## Wave 4 — not started (0 of 12)

Confirmed absent: `as_of` on `AgentTurnContext` (W4.7 — zero references).

## Wave 5 — not started (0 of 11)

Confirmed absent: `FUZZY_SEARCH` (W5.1 — zero references). W5.9's stray
`backend/poetry.lock;W/` and `backend/pyproject.toml;W/` are still present.

## Work done outside the plan

Not scheduled, and it came at the cost of Wave 2's tail. Recorded so the ledger is honest:

- **Identification signals.** Of the eight signals the flow calls for, the reachable agent
  searched on four. Street, city, state and postal code were declared, validated, then dropped —
  on grounds that stopped being true once the schema grew properties for all of them. Email and
  phone were not on the intent at all, while the clarification policy ranks them at 95 and 90, so
  the agent asked for an email and had nowhere to put the answer. This is the class of defect
  W5.2's evaluation suite exists to catch.
- **Six ineffective `sparse` indexes**, one of them `unique` over a field written as explicit
  `null` — meaning a session-less support case could be created exactly once, ever. `sparse`
  omits a document only when the field is *absent*.
- **Replay provider and manual-handoff selection**, taken from the predecessor platform's
  simulator: recorded answers replayed, a miss calling the real provider and recording it. This
  makes W5.2's evaluation suite affordable to run repeatedly.
- **Seed data generator** (`scripts/generate_seed_data.py`) driven by
  `config/seed/generation.yaml`. Written and committed, **never executed**.

## Verification: first full run

Run 2026-08-12 in the diagnostics container: **2,392 passed, 56 failed, 10 skipped, 4 errors**
(47 min). Waves 0-2, the three merged worktrees, the index migrations and the sync-projection
change all hold against real infrastructure.

**The 56 failures are an environment artefact, not code.** All are
, all the same assertion:



 is Neo4j on IPv6 loopback. Those tests construct their own , which reads
the mounted repository-root  -- and that file was rewritten to literal  values
when Vault was disabled. The Vault placeholders it replaced were host-agnostic; the literals are
not, and inside the container they are wrong.  overrides do not help: they reach
the process environment, and  re-reads the file underneath them.

**Fix:** give the container a  carrying in-network values -- ,
, , ,  -- rather than passing hostnames as
 flags. Then re-run .

Two hypotheses were wrong before this one: that  starved the
scenarios, and that dropping the synthetic // collections removed
their fixture data. Both were inferred from where failures clustered instead of from reading one
assertion -- which is what execution rule 3 exists to prevent.

**Still open:** 4 errors in 
(SQL Server is up; likely a missing table or driver in that container). And  is
worth enabling for the next full run so it records itself and later runs cost nothing.

**Also note:** piping a container run through `tail` loses the entire output if the connection
drops -- it happened twice. Write to a file inside the container and tail that afterwards.
