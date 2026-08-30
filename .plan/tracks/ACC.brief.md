# ACC — Acceptance & hardening

Self-contained brief. Read `.plan/contracts.md` + the Verification section of the approved plan. Branch `feat/acc-acceptance` off the RV-approved commit after V3 merges. Ledger `.plan/tracks/ACC.ledger.md`; commits `(ACC) step:<id> …`. Owns `backend/tests/` additions and `backend/tests/harness/` only — no production-code edits (a needed production fix returns to the owning slice via the orchestrator).

## Scope

1. **Kill/restart harness** — `backend/tests/harness/chaos_restart.py`: drives worker/API kill + restart against live infra; live-infra modules use the `_real_infra.py` suffix + `live_infra` marker (anchors: `backend/tests/conftest.py:48` `_LIVE_INFRA_MODULE_SUFFIXES`, `pyproject.toml` markers, `scripts/dev/run_real_infra_suite.sh`).
2. **Business-calendar fixture** — a Mon–Fri 09:00–17:00 calendar (production.yaml dev calendar is 24/7 and insufficient); scenarios 13/19: per-case cadence max 3 across N reviews, no duplicate reminders across a clarification round-trip, weekend-spanning restart fires no retroactive burst.
3. **Durability scenarios (14–18, 20, 23)** per the plan's Verification section: panel reload reconstruction; kill mid-review (draft + edit rows + remaining timeout survive); kill mid-send → exactly one message on B (delivery identity + receiver dedupe); kill after RMA before relay → one relay, one omc write; inbound-stream ordered drain; causal ordering (outbound waits for its inbound's classification; unrelated approval doesn't); dead-letter parks only its stream; analysis-record determinism across provider fallback and crash-after-extraction; delivery failure surface (a/b/c); `SUPPORT_REPLY` aggregate parity; predecessor validation; legacy-identity fact replay; deploy replay on recorded histories (both patch branches); resolver mid-graph resume.
4. **Context scenarios (21–22):** byte-identical `assemble_case_context` across kill/restart; compaction keeps pinned facts, loses none; pinned analysis release across a mid-retry config promotion.
5. **Panel load test** at the contracts.md §2.5 volume (200 concurrent cases, 10s poll) — result gates the shipped `copilot.case_poll_interval_ms`; hash-stability (timer ticking → identical ETag), stale-source end-state scenario, two-principals-identical-ETag, cache-header assertions.
6. **Wait-equivalence test** for the registered `_await_policy_override`/template-wait duplication (contracts.md §10 follow-up condition).
7. **Fabrication-guard extension** — extend `ReturnCopilotFabrication.test.ts`-style source guards and add a backend grep-test banning fact-name string literals outside `operations/fact_names.py` (RV's grep, made durable).
8. **A11y sweep** — extend the Playwright/axe route sweep to the panel and template-config routes; keyboard path review→edit→send; mid-edit artifact arrival focus test.
9. **Acceptance run** — execute the full 26-item gate against live infra (`run_real_infra_suite.sh` + the browser suite); record output under `.plan/acceptance/`.
10. **Retirement proposal** (document only, post-green): `compose_support_handoff` composition path + any superseded surface — RV-gated, executed after the gate is green, not by ACC.

## Definition of done
All 26 acceptance items green against live infra with output recorded; every new scenario in the normal or live-infra suite per the filename convention; ledger + delta report committed.
