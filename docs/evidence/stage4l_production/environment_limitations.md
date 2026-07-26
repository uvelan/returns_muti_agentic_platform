# Stage 4L Validation Environment Limitations

- Python available: 3.13.5.
- Node available: 22.16.0; the frontend requires Node 24 and npm 11.
- Docker daemon is unavailable.
- Poetry is unavailable.
- Dependency modules required by the full backend runtime are unavailable in this packaging environment: PyMongo, Neo4j, Temporal, and pymssql.
- Ruff and mypy are unavailable.
- `node_modules` is not installed and npm restoration was not performed.

Consequently, the following gates were not executed here:

- dependency-backed full pytest suite;
- Ruff;
- strict mypy;
- FastAPI OpenAPI export and generated TypeScript contract check;
- frontend ESLint, full TypeScript project typecheck, Vite build, Vitest, Playwright, and accessibility tests;
- live MongoDB, SQL Server, Neo4j, Temporal, Valkey, OMC, carrier, notification, or external-ticket integration tests.

The delivered classification is `SOURCE_VALIDATED`. It is not `PRODUCTION_VALIDATED`.
