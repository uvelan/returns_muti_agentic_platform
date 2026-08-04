# Repository Agent Instructions

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
11. Commit and push every completed step to `feat/v2-order-discovery-integration`.
12. Do not create unnecessary branches or ZIP files.
13. Continue to the next ready task unless a documented blocker prevents safe progress.

## Task execution level

### SMALL

For documentation corrections, test expectation updates, typing fixes, context maintenance and isolated low-risk defects.

```text
Codex implementation
→ Gemini 3.6 Flash validation
→ Codex commit and push
```

### NORMAL

For backend services, connectors, APIs, frontend features, configuration contracts and repository integrations.

```text
Gemini 3.6 Flash focused analysis
→ Codex implementation and tests
→ Sonnet 4.5 independent review
→ Gemini 3.6 Flash independent validation
→ Codex commit and push
```

### CRITICAL

For graph rebuild, distributed locking, idempotency, authorization, prompt-injection controls, secrets, migrations, cross-agent boundaries and on-demand synchronization.

```text
Gemini 3.1 Pro architecture analysis
→ Codex implementation
→ Sonnet 4.5 code and security review
→ Codex correction loop
→ Gemini 3.6 Flash independent validation
→ Codex commit and push
```

Do not use more agents than the task risk requires.

## Git rules

Before each implementation step:

```powershell
git fetch --all --prune
git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/feat/v2-order-discovery-integration
git pull --ff-only origin feat/v2-order-discovery-integration
```

After review and validation:

```powershell
git diff --check
git diff --stat
git add <explicit-files>
git commit -m "<type>(<scope>): <completed result>"
git push origin feat/v2-order-discovery-integration
git fetch origin
git rev-parse HEAD
git rev-parse origin/feat/v2-order-discovery-integration
git status
```

Do not use `git add .` unless every changed and untracked file has been inspected.

## Evidence language

```text
UNKNOWN — repository evidence is unavailable
```

```text
BLOCKED — <exact blocker>
```
