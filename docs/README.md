# Documentation

**Current as of 2026-08-14, commit `dcbb7dc`, branch
`refactor/unified-return-platform`.**

Everything in this directory describes how the platform works **now**. Superseded
plans and status documents are under [`archive/`](archive/README.md) and describe
nothing current.

## Start with the flow

[**`architecture/canonical-runtime-flow.md`**](architecture/canonical-runtime-flow.md)

One path runs a return from an associate's first sentence to a persisted,
graph-visible RMA. Read it before anything else. Several of the platform's hardest
defects were not bugs inside a stage but breaks *between* stages — a case that existed
with nothing to advance it, a shipment written where fulfilment never looked — and
those are invisible if you only ever read one module.

## Architecture

| Document | Subject |
|---|---|
| [`canonical-runtime-flow.md`](architecture/canonical-runtime-flow.md) | The whole path, stage by stage, and the nine invariants it holds |
| [`order-discovery.md`](architecture/order-discovery.md) | How an order is found |
| [`identification-fields.md`](architecture/identification-fields.md) | The runtime field catalogue. Adding the tenth field needs no code |
| [`bay-assignment.md`](architecture/bay-assignment.md) | Best-effort placement, and why a partial result is not a result |
| [`rma-and-shipment.md`](architecture/rma-and-shipment.md) | `Case → N RMAs → N items`, and RMA-scoped shipment state |
| [`ai-dispatch.md`](architecture/ai-dispatch.md) | One dispatch boundary, interception, redaction ordering |
| [`graph-generations.md`](architecture/graph-generations.md) | Generations, fencing, migration classification, cutover |
| [`graph-analyzer.md`](architecture/graph-analyzer.md) | Host-composable schema analysis, independent of the returns business |
| [`configuration-adoption.md`](architecture/configuration-adoption.md) | Releases, hot adoption, and why `ACTIVATED` is not `LIVE` |
| [`security-boundaries.md`](architecture/security-boundaries.md) | Ownership, the source read-only policy, secrets, authorization |

## Screens

[`screens/`](screens/README.md) — one functional document per screen, on a common
template: purpose, UI regions, actions, APIs consumed, live-state behaviour,
loading/error/empty states, persistence, audit effects, configuration dependencies,
known constraints.

Nine domains and a landing page. Before this directory existed, screens were
documented only by inline TSX comments.

## API

[`api/README.md`](api/README.md) — for each surface: caller role, idempotency key and
semantics, concurrency control, side effects, configuration-release behaviour, audit
effects, error taxonomy. 136 paths, organised by surface because those seven
dimensions are properties of a surface.

## Configuration

[`configuration/families.md`](configuration/families.md) — per family: security
classification, bootstrap-only vs runtime-editable, hot-change support **stated
separately for API and worker processes**, propagation, in-flight case behaviour,
rollback implications, adopted-release readback.

[`CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`](CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md)
— the decision record behind the lifecycle.

## Optimization

[`optimization/`](optimization/README.md) — each optimization with its problem, scale
assumption, strategy, **correctness invariant**, indexes, caching and invalidation,
consistency tradeoff, fallback, limits, observability and failure mode.

The correctness invariant is the load-bearing section. The search defect this platform
carried was exactly the shape of an optimization with none.

## Operations

| Document | Answers |
|---|---|
| [`startup.md`](operations/startup.md) | How do I bring it up, and how do I know it came up |
| [`shutdown.md`](operations/shutdown.md) | What is safe to interrupt |
| [`reset.md`](operations/reset.md) | How do I get back to a clean, fully-built environment |
| [`recovery.md`](operations/recovery.md) | What repairs itself, what needs me, what needs neither |
| [`troubleshooting.md`](operations/troubleshooting.md) | The symptom points somewhere other than the cause |

Read `troubleshooting.md` before debugging an environment problem. Every entry in it
cost real time to diagnose, and the top half is about the machines rather than the
platform: Windows reserving the TCP range Neo4j sits in, exported placeholders
overriding a working `.env`, worktrees created hundreds of commits stale.

Also: [`SEED_DATA_GENERATION.md`](SEED_DATA_GENERATION.md) and
[`code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md`](code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md).

## Plan, state and audit

| Document | Subject |
|---|---|
| [`UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`](UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md) | Where the platform is going |
| [`UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md`](UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md) | The plan being executed |
| [`UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`](UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md) | Where execution stands |
| [`UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md`](UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md) | What was found wrong, with code evidence |
| [`DOCUMENTATION_REMEDIATION_LEDGER.md`](DOCUMENTATION_REMEDIATION_LEDGER.md) | Every documentation defect and its disposition |

The audit is anchored at `0615921` and the code has moved well past it. Where the two
disagree, the code and these documents are current — the ledger records which audit
items were superseded and why.

### Return Copilot

A second, narrower programme with its own audit and plan. Scoped to the Return Business Copilot
and its backend integration; anchored at `2878be0` plus the unpushed Copilot working tree.

| Document | Subject |
|---|---|
| [`RETURN_COPILOT_AUDIT_2026-08-15.md`](RETURN_COPILOT_AUDIT_2026-08-15.md) | Sixteen findings, each marked runtime- or statically-confirmed |
| [`RETURN_COPILOT_REMEDIATION_PLAN.md`](RETURN_COPILOT_REMEDIATION_PLAN.md) | The plan closing them, with its amendment history |
| [`RETURN_COPILOT_PARALLEL_EXECUTION.md`](RETURN_COPILOT_PARALLEL_EXECUTION.md) | What can run concurrently, with measured file contention |
| [`RETURN_COPILOT_POLICY_BASELINE.md`](RETURN_COPILOT_POLICY_BASELINE.md) | The return eligibility rule set the deterministic evaluator implements |

The Copilot audit describes the tree at its own baseline and is **not** superseded by the
`0615921` audit above — the two cover different subsystems at different times. The remediation
plan is the authority for Copilot work; where it and the Copilot audit disagree, the plan is the
later document.

## Process

[`implementation/`](implementation/) and [`execution-context/`](execution-context/) —
how work is carried out. These describe process, not platform behaviour, so they do
not compete with the documents above.

## Evidence

[`evidence/`](evidence/) — validation receipts and gate outputs. Point-in-time records
of what ran. A receipt does not stop being a record of its run, so nothing here is
ever "superseded".

## Archive

[`archive/`](archive/README.md) — twenty-eight superseded plans, status reports, review
verdicts and design prompts, with the supersession named per group. **Nothing in it
describes how the platform works today.**

## Module-local documentation

Several packages carry their own README with implementation-level detail:

```text
backend/config/README.md                                what each packaged YAML is
backend/src/return_platform/agents/README.md
backend/src/return_platform/ai/README.md
backend/src/return_platform/bootstrap/README.md         and bootstrap/adapters/
backend/src/return_platform/configuration/README.md
backend/src/return_platform/graph_schema_analyzer/README.md
backend/src/return_platform/platform/*/README.md        kernel packages
frontend/README.md                                      the frontend runbook
```

Module docstrings in this codebase are unusually substantial and are frequently the
best available explanation of *why* a mechanism has its shape. The documents here
summarise and connect them; they do not replace them.

## Conventions in these documents

Each document states the commit it is current as of. When a document explains a
mechanism, it also states what the mechanism replaced and why — a design without its
rejected alternative is a design nobody can review, and most of the defects in the
audit were introduced by a change whose reasoning was never written down.
