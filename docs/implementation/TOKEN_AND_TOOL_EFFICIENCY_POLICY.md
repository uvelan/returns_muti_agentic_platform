# Token and Tool Efficiency Policy

## Purpose

Reduce token usage, repeated analysis, duplicate validation and unnecessary tool calls without weakening implementation quality.

## Required behavior

Agents must:

- Read relevant execution context before searching the repository.
- Reuse evidence tied to an unchanged relevant commit.
- Inspect assigned files before broader repository areas.
- Use Git diffs to identify the current delta.
- Run focused checks during implementation.
- Run broader gates only at phase and final validation.
- Keep contexts concise and delta-based.
- Reference prior contexts instead of copying them.
- Use deterministic code for schema, permission, checksum, idempotency and query validation.
- Stop repeated test execution when no relevant change occurred.
- Cancel duplicate work.

## Inspection order

```text
Assigned files
→ direct imports and callers
→ related tests
→ public contracts
→ broader repository only when required
```

Useful commands:

```powershell
git diff --name-only <starting-commit>..HEAD
git diff --stat <starting-commit>..HEAD
git diff <starting-commit>..HEAD -- <relevant-path>
```

## Full repository scans are reserved for

- Phase 0 baseline
- Legacy-removal proof
- Repository-wide security review
- Import-boundary validation
- Phase integration gates
- Final validation

## Validation reuse

A passing result may be reused when relevant code, configuration, dependencies and fixtures are unchanged.

After correction:

1. Run the previously failing test.
2. Run directly related tests.
3. Run the required focused regression set once.

## Context rules

Do not copy:

- the master prompt
- previous contexts
- entire source files
- complete logs with irrelevant output
- unchanged architecture summaries

Record only new inspection, changes, commands, findings, evidence and unresolved items.

## Model-call rules

Do not use an LLM for deterministic:

- schema validation
- identifier validation
- permission enforcement
- query-plan validation
- checksums
- idempotency
- concurrency control
- formatting
- test-result parsing

For runtime agent turns:

- send only relevant recent history
- reuse schema details already loaded
- use validated summaries for older history
- avoid repeated tool results
- stop at terminal state
- enforce reasoning-step budgets
- cache immutable schema summaries by release

## Repeated-failure rule

After three materially identical failures with no new diagnostic signal:

```text
BLOCKED — repeated failure with no new diagnostic signal
```

Record attempted corrections and required missing information.
