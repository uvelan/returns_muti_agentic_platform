# ACC phase 2 — acceptance scenarios and the 26-item gate

Self-contained brief. Read `.plan/contracts.md` (all of it — **§1a now carries five amendments that change what several scenarios must assert**), this brief, and `.plan/merge.md`'s "Recurring failure shapes" section before writing a line. Branch `feat/acc-scenarios` off the RV-approved trunk head named at dispatch. Ledger `.plan/tracks/ACC.ledger.md` under a `phase 2` heading; commits `(ACC) step:<id> …` with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**You own `backend/tests/` additions only. No production-code edits.** If a scenario cannot pass because production is wrong, **stop and report** — the fix belongs to the owning slice under review. That has happened repeatedly on this run and is the expected outcome, not a failure.

ACC phase 1 (merged, `c1c2b0f`) already shipped: the fact-name AST guard, the Mon–Fri business-calendar fixture, and `tests/harness/chaos_restart.py`. Build on them.

---

## Dispatch conditions (inherited from reviews; these are requirements, not suggestions)

1. **Business-time scenarios must assert, not assume.** The Mon–Fri fixture's id is deliberately not `default`, which prevents *shadowing* — but a scenario that simply never calls `with_business_calendar` silently inherits the shipped 24/7 dev calendar, runs on wall clock, and stays green while proving nothing. **Every scenario depending on business time must assert `calendar_applied is True` or `not …is_continuous`.** Both instruments exist and are proven. Decide at the start whether to build the harness-level guard RV suggested — its shape was undecidable in phase 1 because V1–V3 surface didn't exist; it does now.
2. **Run the never-executed safety nets FIRST, before anything is built on them.** Two of the harness's guarantees are arguments rather than observations: (a) `test_chaos_restart_smoke_real_infra.py` has never run — the datastores were down; (b) the behavioural SIGTERM pin is Windows-skipped, and RV narrowed its single unproven link to precisely whether `os.killpg(getpgid(pid), SIGTERM)` reaches the child through the session established at launch (everything upstream — spec construction, script generation, launch, `stop()`, `kill()` — was proven to execute). If either is broken, every kill/restart scenario rests on sand.
3. **Acceptance 18's ordered drain rests on V2 populating the causation chain.** The ordering machinery is S2's; the guarantee is only real if the chain is actually filled. **Assert the chain, not just the drain.**

## Amendments that change specific scenarios

- **AMENDMENT-3** — ingress is `POST .../work-items/{id}/inbound-messages`. The old path still serves two live associate handlers (`add_message` POST, `list_messages` GET). Scenario 7 and any ingress scenario must use the new path, and something must assert **all three** endpoints coexist.
- **AMENDMENT-4** — the omc mirror is **eventually once, not atomically once**: convergent idempotence in a crash-safe order (merge → mirror row → outbox row, each a no-op on repeat), driven by at-least-once redelivery. **Scenario 17's observable is unchanged** ("relayed once, correct record, no duplicate omc write") but the mechanism is not a transaction — do not write a scenario that asserts atomicity.
- **AMENDMENT-5** — recovery: `DELIVERY_FAILED → APPROVING` requires a **live execution** (else 409), and the gate moves every non-terminal review to `HELD_FOR_OPERATIONS` on close. Scenarios must include: a retry against a closed gate 409s rather than silently transitioning, and **no review is ever left in a state with no legal exit**. Assert the *absence* of the stranded state.
- **AMENDMENT-1 / -2** — `literal:` is a legitimate fourth binding source; `return_record:` reaches only declared projection attributes.

## Scenario groups (the 26-item gate)

Map each to a named test. Where an item is already covered by an in-slice test, **say so and verify it rather than duplicating it** — but check it isn't one of the known blind shapes first.

- **1–2 config-only rendering:** rename a source binding in config and see the output change with no code edit; parcel-vs-LTL variant selection from product data; batched sync (N missing graph bindings → one sync per source).
- **3–6 review gate:** draft visible and unsent until approved; edit → fact + diff + sent-payload equality; `hold` never auto-sends; `auto_send` refused for each guard condition; cancel → redraft; CAS 409s; approval racing a final autosave; two-actor conflict blocks approval; reload restores unsaved edits; bay present/absent. **Plus AMENDMENT-5's two new ones.**
- **7–8 relay + multi-RMA:** NL message with RMA + tracking + label binds to the correct record, reaching both transcript and panel; two records get their own artifacts with no cross-assignment; an unknown reference produces a map-or-reject clarification and **never a phantom record** (DR-11); a prompt-injection fixture cannot steer tool selection or supply an argument.
- **9–12 resolver:** facts-answerable → autonomous answer with provenance **and disclosure line**; tool route with no credential in checkpoints or logs; unresolvable → verbatim clarification that relays and unblocks; sub-threshold → no answer; gated reply visible and approvable; budget exhaustion; authz 403/404 with no fact written.
- **13, 19 timers:** per-case cadence, max 3 total across N reviews; no duplicate reminders across a clarification round-trip; a restart spanning non-business hours fires no retroactive burst. **All three need condition 1's assertion.**
- **14–18, 20, 23 durability:** panel reconstructed after reload; kill mid-review (draft + edit rows + remaining timeout survive); kill mid-send → exactly one message on B; kill after RMA before relay → relayed once, one omc write; downtime backlog drained in order per case; causal ordering (outbound waits for its inbound's classification, unrelated approval does not); deploy replay on recorded histories for **both** patch branches; resolver retry resumes at the last completed node.
- **21–22 context:** byte-identical `assemble_case_context` across kill/restart with the tokenizer pinned; compaction keeps all pinned facts and loses none; the analysis release stays pinned across a mid-retry config promotion.
- **24–25 frontend:** full keyboard path review → edit → send; a support artifact arriving mid-edit steals no focus and drops no edit; design-token reuse; `contracts:check`; MSW conformance; `CaseOperationsPage` consuming the same payload.
- **26 RV:** every merged branch has a recorded `PASS` — verify against `.plan/reviews/`; the seeded-hardcoding fixture was caught in the calibration round (`.plan/reviews/calibration-1.md`).

## How to write these tests

This run's reviewer proves findings by fault injection, and **every slice has shipped at least one green-but-blind test**. Read `.plan/merge.md`'s "Recurring failure shapes" list — nine shapes, each earned. In particular: a double that accepts anything proves nothing; a consumer tested against a synthetic producer proves nothing about producers; nobody standing at the seam is how a signal fails to decode silently; and a test whose inputs cannot exercise the property is green for the wrong reason. **For every scenario, inject the fault it exists to catch and record before/after evidence in the ledger.** A scenario that cannot be made to fail is not a scenario.

## Definition of done

All 26 items green against live infra, with output recorded under `.plan/acceptance/`. Each scenario in the normal or live-infra suite per the filename convention (`_real_infra.py` suffix + `live_infra` marker). Ledger and delta report complete. Any production defect found is **reported, not fixed**.
