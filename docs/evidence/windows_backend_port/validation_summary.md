# Windows backend/data/AI port evidence

Baseline: `97836a710aad7464ab6a233c06f127bd9fbb1a23` on
`feat/production-order-discovery-copilot`.

This is Windows implementation evidence only. It is not sandbox or production
validation.

## Results

- Ruff focused check: passed.
- Strict mypy for all 13 changed backend source/test files: passed.
- Focused pytest matrix: 49 passed.
- Full backend pytest: 1,111 passed, 3 skipped. The test process used
  `PLATFORM_VAULT_ENABLED=false` so the root `.env` remained untouched and
  lifespan unit tests did not contact the configured live Vault endpoint.
- Final JSON seed scale: 10,000 customers, 20,000 products, 1,000,000
  orders, 1,000,000 shipments, and 1,249,998 projected order lines.
- Repository-wide coverage: 66.50% (1,111 passed, 3 skipped), below the
  configured 90% gate. The gap is repository-wide and concentrated in existing
  API, infrastructure, provider, and orchestration modules; changed-file tests
  passed.
- Repository-wide strict mypy: 13 pre-existing errors remain in unrelated
  operational-generation/configuration test files.
- Repository-wide Ruff lint: passed. Repository-wide Ruff format check reports
  two pre-existing unformatted operational-generation files; all changed
  Python files are formatted.
- Stage 4 source validation: passed.
- Stage 4 contract validation: passed.
- Unchanged frontend syntax validation: passed (188 files parsed).

## Linux-only remainder

Dependency-backed seed application, graph count readback, live AI-provider
validation, browser E2E, accessibility, restart/replay, and production evidence
remain authoritative Linux checks.
