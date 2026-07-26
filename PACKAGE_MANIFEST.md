# Production Return Platform Package Manifest — Stage 4M

Generated: 2026-07-25

## Classification

```text
SOURCE_VALIDATED
LIVE_STACK_VALIDATION_PENDING
```

This package extends the production application with development/test dependency simulators. It is not a separate demo application.

## Included

- Stage 4L production return agents, workflows, configuration, APIs and role-specific screens.
- Stage 4M deterministic OMC, Parcel, Freight/TMS and LSI simulators.
- Lightweight optional AI narrative enrichment with mandatory templates.
- Complete simulator AI-attempt and fallback metrics.
- Transactional-outbox simulator dispatchers.
- Dedicated simulator pages and operation details.
- API-driven E2E scripts for branch parcel and offsite heavy returns.
- Focused tests, source validators, runbooks and evidence.
- Safe `.env.example` with no credentials.

## Excluded

- `.git/`
- `.env` and credentials
- `node_modules/`
- frontend build output
- Python virtual environments
- Python bytecode and caches
- pytest/mypy/Ruff caches
- coverage files
- browser-test artifacts
- root runtime logs

## Required validation before promotion

Run the complete backend, frontend, Compose, Temporal restart, Playwright, accessibility and live-provider gates documented in:

```text
docs/STAGE_4M_FUTURE_PRODUCTION_STEPS.md
```
