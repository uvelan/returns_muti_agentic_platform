# 03 · Task board

**Writer:** coordinator only. Single source for status, owner, dependencies and
commit hashes. `context_version: 2`.

Status values: `NOT_STARTED | IN_PROGRESS | BLOCKED | COMPLETE`.
A task is `COMPLETE` only with an accepted handoff carrying a commit hash, a file
list and a scoped test result.

---

## Wave 0 · Coordinator only

| ID | Task | Owner | Status | Result |
|---|---|---|---|---|
| W0-1 | Fetch remote, record baseline commit, confirm clean tree | Coordinator | COMPLETE | `47f5abd` on `refactor/unified-return-platform`, tree clean |
| W0-2 | Verify referenced paths, symbols, topics, seams | Coordinator | COMPLETE | All verified; two conflicts → D-1, D-2 |
| W0-3 | Create context directory and seven files | Coordinator | COMPLETE | this directory |
| W0-4 | Record measured test baseline | Coordinator | COMPLETE | 4025 passed, 3 skipped; 1 known ruff error BF-1 |
| W0-5 | Compute contracts hash, set `context_version` | Coordinator | COMPLETE | `context_version=1`, hash recorded in 00 |
| W0-6 | Create integration branch and three worktrees | Coordinator | COMPLETE | `feat/teams-bots-windows-first` @ `56fd1f5`; 3 worktrees |
| W0-7 | Resolve ownership collisions | Coordinator | COMPLETE | D-4, D-5; `06-ownership.md` |

**Gate W0** — all six complete, no overlapping writable files, every contract
defines its schema and error mapping, every agent has a scoped test command.

## Wave 1 · Three agents in parallel — independent foundations

| ID | Task | Owner | Depends on | Status |
|---|---|---|---|---|
| W1-A | Gateway skeleton: two listeners, config validation, SDK-authenticated bot endpoints, reference/nonce/activity repositories with indexes, unit tests with no live Teams | A | Gate W0 | NOT_STARTED |
| W1-B | Platform config (C3 Python half only), HMAC client (C2), dispatcher registration and response classification against a local stub. **Do not wire producers yet.** | B | Gate W0 | NOT_STARTED |
| W1-C | Saga model/repository, canonical request hash, SQL idempotency constraint, create-or-recover, leased reconciliation foundation, crash-window unit tests | C | Gate W0 | NOT_STARTED |

**Gate W1** — context receipts valid, scoped tests green, ownership and secret
boundaries checked, integrate in order A → B → C, run only impacted tests, push.

## Wave 2 · Three agents in parallel — business wiring and Windows operations

**Prerequisite (human, not an agent):** `devtunnel` installed, two bot
registrations, two Teams packages installed in the target group chat, HMAC secret
generated. Procedure and checklist: `07-credentials-procedure.md`. Wave 1 does not
need any of it.

| ID | Task | Owner | Depends on | Status |
|---|---|---|---|---|
| W2-A | Internal HMAC endpoints, proactive delivery, cards, graceful shutdown, two manifests, Windows scripts | A | W1-A | NOT_STARTED |
| W2-B | Wire the two producers (integration map flows 1 and 2), idempotency and contract tests against a gateway stub, verify no credential leakage | B | W1-B, W2-A contract | NOT_STARTED |
| W2-C | Mongo transaction + evidence-driven reconciliation, outbox creation through the repository seam, concurrent lease / hash-conflict / kill-window tests | C | W1-C | NOT_STARTED |

**Gate W2** — contract compatibility reviewed; **cross-language golden fixtures
prove a Python-generated HMAC request is accepted by Node without body
re-serialisation**; both manifests validate with different bot ids; scoped tests
pass; push.

## Wave 3 · Windows integration and adversarial validation

Three independent validation lanes against the integrated branch. Agents do not
modify production code unless the coordinator assigns a defect.

| ID | Lane | Owner | Status |
|---|---|---|---|
| W3-A | Teams/gateway: install both apps, reference capture, gateway-alone proactive send, non-tenant + duplicate activity + invalid token + stale reference + bot-loop, `404` on both `/internal` routes through the tunnel | A | NOT_STARTED |
| W3-B | Platform/outbox: both producer paths, topic/routing/payload, HMAC rejection cases, retry mapping, duplicate-command prevention, no secret leakage | B | NOT_STARTED |
| W3-C | Saga/recovery: termination injected at every window, leased reconciliation creates no second RMA, hash-conflict behaviour, Teams-outage independence | C | NOT_STARTED |

**Windows final gate (coordinator):** full backend gate once, gateway
lint/typecheck/unit/integration once, repository lint and generated-contract
checks, regenerate OpenAPI + TS **only if** request/response models changed,
record results, commit and push the Windows acceptance receipt.

**Linux work is forbidden until that receipt exists.**

## Wave 4 · Linux port

| ID | Task | Owner | Status |
|---|---|---|---|
| W4-A | Linux setup/start/tunnel/validate scripts, **new** tunnel exposing only 3978 | A | NOT_STARTED |
| W4-B | Worker/gateway portability, HMAC byte equivalence, env loading, restart behaviour | B | NOT_STARTED |
| W4-C | Saga persistence, SQL uniqueness, Mongo transactions, leased recovery on Linux | C | NOT_STARTED |

**Linux final gate:** update both bot registrations to the Linux tunnel URL
**before** testing, repeat the Windows behavioural gates without redesign, run full
validation once, record receipts, push.

## Scoped test commands

| Agent | Command |
|---|---|
| A | `npm test` in `services/teams-gateway/` (plus lint and typecheck) |
| B | `backend/.venv/Scripts/python.exe -m pytest tests/operations/integrations tests/configuration -q` |
| C | `backend/.venv/Scripts/python.exe -m pytest tests/operations -q` |

Full-repository gates run **once** at the Windows integration gate and **once** at
the Linux final gate — not during implementation tasks.
