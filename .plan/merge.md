# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Planned order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.
**Deviation in force (user-directed, ≤3 parallel agents):** slices are pipelined off the preceding slice's *candidate* head rather than waiting for its merge, with rebase instructions if review forces changes. ACC phase 1 was cut early because its scope is test-only and independent. Every pipelined base has so far matched the eventually-approved head.

## Slice status

| Slice | Branch | Status | RV rounds | Merged at |
|---|---|---|---|---|
| T0 | (trunk) | DONE | — | 2cafe2a |
| S1 | feat/s1-model-identity | **MERGED** | 1 · PASS (6bdb5bd) | 5d58b90 |
| S2 | feat/s2-delivery-spine | CHANGES_REQUIRED → fixing · was 4359bf9 | 1 · CR (db7bfb9) | — |
| V1 phase 1 | feat/v1-template-review | CHANGES_REQUIRED → fixing · was d452e97 | 1 · CR (f8ce598) | — |
| V1 phase 2 | (same branch, later) | BLOCKED on S2 merge | — | — |
| V2 | feat/v2-ingress-relay | NOT_STARTED | — | — |
| V3 | feat/v3-resolver-clarification | NOT_STARTED | — | — |
| ACC-1 (harness) | feat/acc-harness | **MERGED** | 2 · CR (ba19fd8) → PASS (9cb3508) | c1c2b0f |
| ACC-2 (scenarios) | not yet cut | BLOCKED on V3 | — | — |
| RV calibration | rv-calibration/seeded-hardcoding | bait CAUGHT as blocking | 1 (d59e017) | never merges |

## Notes per slice

**S1 — merged, clean.** +1136/−0, purely additive: legacy `_append_fact_once`/`latest_case_facts` byte-untouched beside new scoped siblings; binding port has *no create method*, so bind-never-create (DR-11) is structural rather than disciplinary. RV advisories carried: A1 a scoped write using a legacy fact name could shadow a case-level value; A2 the `::` identity-separator assumption, auditable via `identity_version`.

**S2 — under review.** All 8 steps, 4506 passed / 2 failed (known pre-existing pair), 0 introduced. Resume correctly applied repo-beats-ledger: found an uncommitted 1033-line `review_aggregate.py`, read it in full, caught a real bug (`self._database[planned.command_document and "case_command_records"]` — truthy by accident, with `"integration_outbox"` hard-coded beside it) and relocated the pair-write into `case_commands.insert_planned()` rather than rewriting the step.
*Deviations:* (a) brief path typo `operations/` → real `workflows/return_case_recovery.py`; (b) append-only additions to S1's `fact_names.py`; (c) new `CaseStatus.AWAITING_TEMPLATE_REVIEW` mapped onto existing `ReturnCaseStatus.AWAITING_SUPPORT` (since `read_persisted_status` refuses unknown statuses, recovery would otherwise raise on the first case V1's gate parks) + forced OpenAPI regen.
*Open risks flagged to RV:* `AWAITING_SUPPORT` is now in `LEGITIMATE_WAIT_STATUSES` — a behaviour change to a pre-existing status, so the time-based sweep no longer relaunches null-`workflowId` cases (the probe-based path still does); RV must confirm the old sweep was not load-bearing. Command-horizon rule unarmed until V1 wires it (degrades honestly). `_wordpiece_approx_v1` is a versioned estimate, not a tokenizer.
*Design note:* `assemble_case_context` types against a structural `ContextPolicy` protocol because `platform/*` may not import `configuration/*` (layering test 13.1 R2a).
*Review round 1 — CHANGES_REQUIRED, three findings, all "multi-write sequences that leave durable state disagreeing with itself":* **F1** the legitimate-wait skip strands the workflow link and **starves the recovery queue** — the flagged risk resolved in the blocking direction, but not for the reason expected: relaunching wasn't the only thing that pass did, it also re-writes `cases.workflowId`, which `return_case_launcher.py:19-25` explicitly names as the repair for a link write it deliberately swallows. Because `list_cases_without_workflow` selects `workflowId: None`, sorts oldest-first and applies a limit, a skipped case never leaves the queue and sits at the head of every batch: RV's probe (100 legitimate-wait cases + 1 genuinely unlaunched, five passes) recovered *nothing*. Monotonic accumulation, so it's a matter of time not chance. Fix = keep refusing to relaunch, stop refusing to repair. **F2** `ensure_case_support_thread.created` is decided by a non-atomic pre-read while its docstring insists only the insert-winner can know it — the loser reports `created=True` having opened nothing, and `created` selects opening-request vs reply, so two replaying workers both compose an opening request: a duplicate to Support through the one field delivery identity doesn't cover. **F3** the conflict marker is bumped/cleared outside the transaction §6 puts it in; a torn pair is unrecoverable both ways.
*Verified clean and not in question:* atomicity of `OPEN → APPROVING` (RV forced the outbox leg to fail mid-approval: review `OPEN`, zero commands, zero outbox rows), the resume bug-fix, the `AWAITING_TEMPLATE_REVIEW` mapping (contract-correct — it appears nowhere in the OpenAPI diff), `ContextPolicy`, purity/determinism of `assemble_case_context`, honest horizon degradation, `absorbed=True` reaching `SENT`, and that `mongo_double.py` genuinely models cross-collection atomicity.

**V1 phase 1 — changes required, fixing.** Items 1, 2, 5 built: template grammar + formatter allowlist + `production.yaml` default block, renderer (batched sync, gaps, within-draft cache), preview endpoint, config UI on the literally-reused `DocumentEditor`.
*Blocking findings:* **F1** the default variant does **not** reproduce today's handoff — the equivalence test was green only because its fixture makes every conditional take the one branch the template can express; RV rendered the shipped yaml through the shipped renderer on a case with no associate and no bay recommendation and found five silent divergences with zero gaps, including customer phone/email (a loss `support_handoff.py` documents as a defect it *fixed*). Phase 2 swaps the composed path behind `workflow.patched`, so these would ship as silent regressions. **F2** five `case_fact:` bindings name facts that exist nowhere in `backend/src`; the assembler seam is docstring-only; `template_preview.py` holds a second uncoordinated copy of the 24-key vocabulary. **F4** `return_record:` resolved via unconstrained `getattr` → closed by AMENDMENT-2.
*Routed to orchestrator:* **F3** → AMENDMENT-1 admits `literal:` as a fourth binding source (frozen enumeration amended, implementation not excused).
*Cleared without finding:* publish path (contract right, my brief wrong), `DocumentEditor` extraction (behaviour-preserving line-by-line), OpenAPI + envelope fix (in-slice), rule 12 outcome gates, test integrity.

**ACC-1 — merged.** Fact-name AST guard (vocabulary read from `fact_names.py` at runtime, AST-based so docstrings stay legal, proved against a planted violation), Mon–Fri calendar fixture, chaos-restart primitives. Test-only, zero production files touched. Round 1 found two rule-10 holes by fault injection — tests that were green but blind; round 2 fixed both with before/after evidence and RV withdrew them only after re-injecting the faults itself, adding a third probe of its own.

## Carry-forward conditions (orchestrator-owned; must be written into the named future brief)

Raised in review and assigned to the orchestrator, not to a slice. A slice cannot inherit these from a ledger note — they go into the brief at dispatch or they evaporate.

### Into the ACC phase-2 brief (from review ACC1-2)
1. **Business-time scenarios must assert, not assume.** The Mon–Fri fixture's id is deliberately not `default`, which prevents *shadowing* — but a scenario that simply never calls `with_business_calendar` silently inherits the shipped 24/7 dev calendar, runs on wall clock, and stays green while proving nothing. Every phase-2 scenario depending on business time MUST assert `calendar_applied is True` or `not …is_continuous`. Both instruments are proven and already exist. The harness-level guard was deliberately not built in phase 1 because its shape depends on V1–V3 surface that did not yet exist; decide at dispatch whether to build it then.
2. **Run the never-executed safety nets first.** Two of this harness's guarantees are arguments rather than observations: (a) `test_chaos_restart_smoke_real_infra.py` has never run — the datastores were down; (b) the behavioural SIGTERM pin is Windows-skipped, and RV narrowed the single unproven link to precisely whether `os.killpg(getpgid(pid), SIGTERM)` reaches the child through the session established at launch (everything upstream — spec construction, script generation, launch, `stop()`, `kill()` — was proven to execute here). The smoke test must be the **first** thing run when the stack comes up, ahead of anything built on it.

### Into the V1 phase-2 brief (S2 handover)
3. **Arm the command-horizon rule.** S2 shipped it unwired: until V1 passes `command_horizon=`, the recovery outcome honestly reports `RELAUNCHED` rather than pretending to have checked.

### Into the V1 phase-2 brief (from S2 review round 1)
5. **Derive workflow ids; never read the link.** `cases.workflowId` can legitimately be null while a case is healthy (S2-1 F1). V1 must derive the id via `return_case_workflow_id(case_id)` rather than trusting the stored link.

### Into the ACC phase-2 brief (from S2 review round 1)
6. **Acceptance 18's ordered drain rests on V2 populating the causation chain.** The ordering machinery is S2's, but the guarantee is only real if V2 actually fills `causation_id` / `required_predecessor_ids[]`. Assert the chain, not just the drain.

### Into the V2 brief (from S1 review)
4. **Watch advisory A1.** A scoped fact write using a *legacy* fact name would surface in `latest_case_facts` and could shadow a case-level value. Nothing in S1 or S2 does this; V2 writes the most new scoped facts and is where it would first appear.
