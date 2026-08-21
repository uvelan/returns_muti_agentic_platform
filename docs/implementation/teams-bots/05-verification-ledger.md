# 05 · Verification ledger

**Writer:** coordinator only. Records commands, scoped results, receipts and known
baseline failures. Append-only.

---

## Measured baseline — commit `47f5abd7fad4e9f0e2c890ef7e762b37e45296e6`

Measured on a **clean working tree** at Wave 0, per D-3. This is the only pass
total any gate may be compared against.

| Command | Result |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest tests -q` | **4025 passed, 3 skipped, 495 deselected**, 170.81s |
| `backend/.venv/Scripts/python.exe -m ruff check src tests` | **1 error** — see known baseline failure BF-1 |

### BF-1 · Pre-existing ruff error, not introduced by this work

```
I001  Import block is un-sorted or un-formatted
  --> tests/dynamic_knowledge/test_a_turn_that_asks_is_not_complete.py:26
```

The file is untouched by this work and the error predates the baseline commit
(introduced in `aa99b6d`). **A gate reporting exactly one ruff error, this one, is
a pass.** Two or more is a regression. No agent may "fix" it as a side effect —
that would put an unrelated change in a Teams commit.

### Frontend

Not measured at Wave 0. This work changes no frontend code. If a
request/response model changes, the OpenAPI snapshots (four committed copies) and
the generated TypeScript must be regenerated and the frontend gate run once — see
the Windows final gate.

---

## Gate results

*(appended as gates are reached — nothing recorded yet beyond Wave 0)*

| Gate | Date | Commands | Result | Receipt commit |
|---|---|---|---|---|
| W0 | — | see above | baseline recorded | pending |

---

## Rules for recording

- Record the **command, exit status, failed test identifiers and the smallest
  useful error excerpt**. Never paste a whole log.
- A claim without a commit hash, a file list and a validation result is not
  accepted as a handoff.
- Full-repository gates run **once** at the Windows integration gate and **once**
  at the Linux final gate. Implementation tasks run scoped tests only.

---

## Gate W0 — PASSED

| Check | Evidence |
|---|---|
| All Wave 0 tasks complete | `03-task-board.md`, W0-1 … W0-7, zero unfinished rows |
| Every referenced path exists or has a creation target | verified; `services/teams-gateway/`, `teams-apps/`, `docs/implementation/teams-bots/` declared as creation targets |
| No two agents share a writable file | `06-ownership.md`, 12 declared paths, **0 collisions** after D-4 and D-5 |
| Contracts define message schemas and error mappings | C1–C8 in `01-frozen-contracts.md`, including the full Bot Connector → outbox status table |
| Each agent has a scoped test command | `03-task-board.md` § Scoped test commands, 3 commands |
| Baseline measured on a clean tree | 4025 passed, 3 skipped; 1 known ruff error BF-1 |
| Integration branch and worktrees from one baseline | `feat/teams-bots-windows-first` @ `56fd1f5`; `task/teams-gateway`, `task/teams-platform-integration`, `task/teams-rma-saga` |

Two conflicts and two ownership collisions were found and resolved **before** any
agent started, which is what this gate is for. Wave 1 may begin.
