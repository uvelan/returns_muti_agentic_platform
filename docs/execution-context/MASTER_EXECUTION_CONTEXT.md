# Master Execution Context

## Repository

- Repository: `https://github.com/uvelan/returns_muti_agentic_platform.git`
- Target branch: `refactor/unified-return-platform`
- Environment: Windows PowerShell
- Primary production-code writer: Seat **W** (model-neutral; see `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md` § 7)

## Current repository state

- Local branch: `refactor/unified-return-platform`
- Local HEAD: `04a05fbfa266c689cfce281df6b2b0b83f1121a3`
- Remote HEAD: `04a05fbfa266c689cfce281df6b2b0b83f1121a3`
- Working tree: clean at verification
- Last verified at: 2026-08-22

## Active programme

The prior phase ledger (`P00-S01` … `P00-S05`, last advanced 2026-08-04 on the superseded v2
order-discovery integration branch) is **closed**. Its task rows described work on a branch
that is no longer the target; they are history and do not describe work in flight. Nothing in
this document directs an agent to a branch other than the target branch named above.

The active programme is the **user-authorized remediation overlay, Execution Plan V4.1**,
which converts the 2026-08-22 deep UI and end-to-end functional audit into dependency-ordered
tasks.

- Overlay ledger: [`remediation/LEDGER.md`](remediation/LEDGER.md)
- Audit baseline: 30 findings, release verdict **NO-GO**
- Authority: `AGENTS.md` § Authorized remediation overlay
- Rule 4 (`do not create another implementation plan`) remains in force. The overlay is
  execution state, not a plan. Agents must not author a successor plan or replan during
  execution.

## Execution topology

One writer at a time. Parallelism is read-only.

| Seat | Writes production code | Responsibility |
|---|---|---|
| **W** | Yes — sole writer | Implements the active task and its focused tests |
| **A1** | No | Prepares the next backend or data task |
| **A2** | No | Prepares route, accessibility, browser and evidence work |
| **R/V** | No | Independent review and validation |

The same seat never implements and independently signs off the same task. Reviewers never
modify production files; corrections return to W.

## Control-document hashes

Read at session start; reread on session restart, context compaction, remote update, or hash
change — not per task.

| Document | SHA-256 |
|---|---|
| `AGENTS.md` | `7344ce7f3ad1e5daeb6f4cbe0d3aa4f44494805238ef26bf6e53f54fac35a7d1` |
| `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md` | `e47a0a4bdc2cbf17b5ea484870653e1ce3ffeb6ca89116ba2189676ab859c3f8` |
| `docs/implementation/ROLE_PROMPTS.md` | `15c7a1b6e6f5fcf906cf3e0a3c35fc3825a041237fde6d181b83efbb61cea299` |
| `docs/execution-context/MASTER_EXECUTION_CONTEXT.md` | recorded in `remediation/LEDGER.md` after this commit |

## Current execution state

- Active programme: Remediation V4.1
- Active task: `T00` — control truth
- Task classification: SMALL
- Current seat: W
- Current status: IN_PROGRESS
- Current blocker: NONE

## Outstanding review findings

### Critical

- NONE

### High

- NONE

### Medium

- NONE

### Low

- R-01: `AGENTS.md` § Git rules pre-step sequence `git status` order
- R-02: `ROLE_PROMPTS.md` Orchestrator role read list path convention missing
- R-03: `ROLE_PROMPTS.md` Repair Loop role missing explicit repeat analysis prohibition

## Blocked tasks

| Task ID | Blocker | Evidence path | Required resolution |
|---|---|---|---|

## Next ready task

- Task ID: `T01a` — truthful gates
- Reason ready: depends on `T00` only
- Required seat: W
- Completion gates: lint, typecheck and unit suites pass; OpenAPI check mode leaves a clean
  tree; the secret scanner satisfies the V4.1 § 11 redaction contract

`P00` (live-route readiness) and `T01b` (live-infrastructure runner) proceed in parallel and
block nothing outside gates L1 and G1 respectively.
