# Repository Agent Instructions

**Current as of 2026-08-14, commit `dcbb7dc`, branch `refactor/unified-return-platform`.**

This file governs how automated agents work in this repository. It describes
*process*, not platform behaviour — for how the platform works, start at
[`docs/README.md`](docs/README.md).

## Authority

1. Read `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md`.
2. Read `docs/execution-context/MASTER_EXECUTION_CONTEXT.md`.
3. Work only on the assigned task.
4. Do not create another implementation plan.
5. Do not replace or reinterpret the approved architecture.
6. Do not hallucinate files, commands, tests, commits, results or runtime evidence.
7. Reuse verified context and inspect only the relevant repository delta.
8. Avoid repeated scans, summaries, validation and model calls.
9. Only one implementation writer may modify production code at a time.
10. Do not commit until required independent review and validation pass.
11. Commit every completed step to `refactor/unified-return-platform`.
12. Do not create unnecessary branches or ZIP files.
13. Continue to the next ready task unless a documented blocker prevents safe progress.

## Task execution level

### SMALL

For documentation corrections, test expectation updates, typing fixes, context maintenance and isolated low-risk defects.

```text
implementation
→ independent validation
→ commit
```

### NORMAL

For backend services, connectors, APIs, frontend features, configuration contracts and repository integrations.

```text
focused analysis
→ implementation and tests
→ independent review
→ independent validation
→ commit
```

### CRITICAL

For graph rebuild, distributed locking, idempotency, authorization, prompt-injection controls, secrets, migrations, cross-agent boundaries and on-demand synchronization.

```text
architecture analysis
→ implementation
→ code and security review
→ correction loop
→ independent validation
→ commit
```

Do not use more agents than the task risk requires.

The model names that used to be written into these three ladders were removed
rather than updated. They named a specific vendor and version for each rung, and
every one of them had already been superseded; a stale model name reads as a
requirement and sends an agent looking for a model it cannot reach. The shape of
each ladder is the instruction — how many independent passes a change of that
risk must survive before it lands.

## Git rules

Before each implementation step:

```powershell
git fetch --all --prune
git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/refactor/unified-return-platform
git pull --ff-only origin refactor/unified-return-platform
```

After review and validation:

```powershell
git diff --check
git diff --stat
git add <explicit-files>
git commit -m "<type>(<scope>): <completed result>"
git fetch origin
git rev-parse HEAD
git status
```

Do not use `git add .` unless every changed and untracked file has been inspected.

### Working in a worktree

Agent worktrees in `.claude/worktrees/` are **not** created at the branch tip.
They have been observed created hundreds of commits stale. Before any work:

```bash
git rev-parse HEAD
git log --oneline -1
```

If HEAD is behind the branch tip and the tree is clean, reset to the tip. If
there is uncommitted work at a stale base, stop and report it rather than
resetting over it. See
[`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md).

## Evidence language

```text
UNKNOWN — repository evidence is unavailable
```

```text
BLOCKED — <exact blocker>
```
