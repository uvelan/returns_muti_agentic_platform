# Task Classification and Model Routing

## Available tools

- Gemini 3.1 Pro
- Gemini 3.5 Flash
- Gemini 3.6 Flash
- Claude Sonnet 4.5
- Codex CLI
- Antigravity IDE

## Ranking for this project

1. **Codex CLI** — production implementation and Git integration
2. **Gemini 3.1 Pro** — critical architecture and blocker escalation
3. **Claude Sonnet 4.5** — independent code and security review
4. **Gemini 3.6 Flash** — focused analysis and validation
5. **Gemini 3.5 Flash** — low-risk context and log maintenance

## SMALL tasks

Examples: documentation correction, isolated test correction, typing fix, context maintenance and small local defects.

```text
Codex
→ Gemini 3.6 Flash validation
→ Codex commit and push
```

Sonnet review is still required when production behavior, persistence, authorization or a public contract changes.

## NORMAL tasks

Examples: backend service, connector, API route, frontend feature, configuration contract and repository integration.

```text
Gemini 3.6 Flash analysis
→ Codex implementation
→ Sonnet 4.5 review
→ Gemini 3.6 Flash validation
→ Codex commit and push
```

## CRITICAL tasks

Examples: graph rebuild, destructive operations, distributed locking, idempotency, conversation concurrency, authorization, secrets, prompt-injection controls, agent boundaries, migrations and on-demand synchronization.

```text
Gemini 3.1 Pro architecture analysis
→ Codex implementation
→ Sonnet 4.5 code and security review
→ Gemini 3.6 Flash validation
→ Codex commit and push
```

## Escalate to Gemini 3.1 Pro only when

- More than three major modules require coordinated change.
- A public contract cannot be preserved.
- A concurrency or transaction defect is unresolved.
- Graph-generation behavior changes.
- Agent ownership boundaries change.
- Security review disputes the design.
- Two targeted repairs fail.
- Repository evidence reveals an architectural conflict.

Do not spend Gemini 3.1 Pro on context files, formatting, routine tests, mechanical adapters or repeated summaries.

## Separation of duties

- Codex writes production code.
- Sonnet reviews normal and critical production changes.
- Gemini 3.6 Flash independently validates.
- The same model must not implement, approve and validate the same task.
