# Production Copilot Linux Implementation Report

## Verdict

The uploaded repository contained a substantial but incomplete Order Discovery Copilot implementation. The implementation was audited, consolidated, and corrected for Linux execution.

Current classification: **SOURCE_VALIDATED**.

The repository is not claimed as fully production-validated because this execution host does not provide Docker, the locked Python development dependencies, the locked frontend dependencies, or the required Node/npm versions. The source, configuration, shell, contract, and syntax gates that can run without those dependencies pass.

## Production capabilities implemented

### Production Order Discovery Copilot

- Persistent MongoDB-backed associate conversations.
- Exact-first discovery for strong identifiers.
- Bounded Lucene fuzzy retrieval for approved natural-language fields.
- Multi-turn customer, order, and line disambiguation.
- Server-owned dialogue state and requested slots.
- Candidate-set identity, expiry, and optimistic concurrency.
- Explicit customer/order/line confirmation.
- Digest-bound Discovery Lock.
- Direct handoff to the existing return workflow.
- Neo4j-first discovery with approved source fallback.
- Targeted graph repair only after strong evidence.
- AI-assisted wording with deterministic fallback.

### Reusable conversation logic

- Domain-neutral progressive conversation state projection.
- Reusable discriminator selection.
- Reusable requested-slot matching.
- Reusable candidate-set generation and expiry.
- Production persistence and orchestration remain inside the Order Discovery service; no disconnected in-memory conversation implementation remains.

### Graph-first configuration control plane

- Immutable configuration releases.
- Single active `ConfigurationHead` per scope.
- Compare-and-swap publication using expected head revision.
- Configuration checksum verification.
- Published release pinning for conversations and workers.
- Last-known-good runtime snapshot behavior.
- Versioned Neo4j migration ledger with migration checksums.

### Vault credential management

- Vault KV v2 infrastructure included in Compose.
- Linux Vault initialization and unseal automation.
- Data-source and AI secret references stored outside application configuration payloads.
- Vault resolution before MongoDB, Neo4j, Valkey, and SQL Server client construction.
- AI keys are not loaded directly from `.env`.
- Secret writes use compare-and-swap semantics.
- Failed graph/receipt persistence invokes Vault rollback.
- Host and container connection references are separated.

### Validation-before-activation

AI credential validation includes:

- endpoint allowlist validation;
- provider authentication;
- model discovery;
- model access validation;
- bounded inference probe;
- structured-output validation;
- task/provider/tier compatibility;
- receipt binding to configuration checksum, secret HMAC fingerprint, and Vault version.

Data-source validation includes:

- connector/source-type validation;
- endpoint allowlist enforcement;
- cloud metadata endpoint blocking;
- TLS validation controls;
- authentication and safe health query;
- dataset discovery;
- required dataset validation;
- code-owned read-only boundaries;
- MongoDB seed-host validation for every explicit host;
- rejection of DNS-based MongoDB seed discovery in the control-plane validator;
- rejection of MongoDB options that disable TLS certificate or hostname verification;
- receipt binding to configuration checksum, secret HMAC fingerprint, and Vault version.

### AI routing

- Configurable provider, key, and model lists.
- Lightweight and standard model tiers.
- Per-key routing and bounded failover.
- Per-key rate and concurrency controls.
- Key cooldown and circuit behavior.
- Prompt safety inspection.
- Strict task contracts.
- Deterministic fallback when all AI routes fail.
- Provider/model/key-profile usage metrics without exposing secret values.

### Contact lookup security

- Phone and email evidence no longer uses enumerable raw SHA-256 digests.
- Normalized, domain-separated HMAC-SHA256 evidence is used.
- The HMAC key is resolved from Vault.
- Graph projection and Copilot lookup use the same evidence algorithm.

### Linux execution

- Host backend and frontend execution preserved.
- Infrastructure services remain containerized.
- Vault added to infrastructure startup.
- Runtime configuration migration/bootstrap is ordered before API and workers.
- Workers resolve the same published graph release and Vault references as the API.
- Existing `.env` files are upgraded without replacing valid values.
- Missing or placeholder local infrastructure credentials are generated without printing them.
- `.env` permissions are forced to `0600`.
- Environment validation fails before Compose starts when required references are invalid.

## Defects corrected

1. Removed the disconnected module-level in-memory Copilot implementation.
2. Fixed graph configuration loading that previously returned YAML values while a graph release existed.
3. Fixed startup ordering that referenced settings before initialization.
4. Added fail-closed production connectivity checks for required runtime stores.
5. Fixed the global exception handler’s invalid `cast()` invocation.
6. Fixed AI routing where one rejected key skipped the remaining keys for the provider.
7. Replaced broad source fallback exception suppression with explicit retriable failure handling.
8. Registered the progressive-disambiguation AI task prompt so it no longer always falls back.
9. Removed fabricated operational metrics and false graph-verification labels from the UI.
10. Removed the incorrect vector-search label for Lucene full-text retrieval.
11. Added dedicated administrator-only Copilot Operations APIs.
12. Prevented configuration publication races using a versioned active-head pointer.
13. Made published configuration releases immutable.
14. Added migration history so Neo4j migrations are not blindly reapplied.
15. Removed broad fuzzy indexes containing sensitive or identifier fields.
16. Fixed host/container Vault DSN confusion that caused containers to connect to themselves.
17. Fixed Vault KV updates that could remove sibling fields.
18. Added explicit secret rollback when receipt persistence fails.
19. Required exact candidate-set identity during confirmation.
20. Added compensation that cancels a newly inserted Discovery Lock when conversation compare-and-swap fails.
21. Added every-seed-host validation for MongoDB DSNs and rejected insecure TLS flags.
22. Added missing JSON content-type headers to configuration and validation clients.
23. Updated repository source gates to verify the actual production Copilot components.
24. Updated Linux README commands and failure behavior.

## Validation results

| Gate | Result |
|---|---|
| Python source compilation | Passed |
| TypeScript syntax parsing, 176 files | Passed |
| Shell script syntax | Passed |
| YAML parsing for Compose and backend configuration | Passed |
| Repository source gate | Passed |
| Source-contract gate, 6 contracts | Passed |
| Git whitespace/error check | Passed |
| Credential-pattern scan excluding local secret state | Passed |
| Environment validation for upgraded root `.env` | Passed |
| Template environment validation | Passed |
| Configuration CAS and secret rollback checks | Passed |
| Contact evidence HMAC checks | Passed |
| Bootstrap-managed endpoint preservation check | Passed |

## Gates not executable on this host

| Gate | Blocking reason |
|---|---|
| Ruff | Locked Ruff package is not installed; configured package registry does not provide the pinned release |
| Strict mypy | Locked mypy package is not installed |
| Full pytest suite | Neo4j, PyMongo, Redis, Temporal, and SQL Server client packages are not installed |
| Frontend typecheck/lint/unit tests | frontend dependency directory is incomplete and package registry returned an availability error |
| Docker Compose startup | Docker is not installed on this host |
| Real browser E2E | Requires complete frontend dependencies, Playwright browser, and running infrastructure |
| Live AI validation | Requires validated provider credentials and outbound connectivity |
| Live data-source validation | Requires running Vault and source infrastructure |

Validation logs are produced outside the project archive under `/mnt/data/returns_validation_logs` on this execution host.

## Linux execution sequence

From the repository root:

```bash
./scripts/bootstrap_host.sh
./scripts/infra.sh start
./scripts/prepare_runtime_configuration.sh
./scripts/run_all_host.sh
```

Verify health:

```bash
curl -fsS http://127.0.0.1:8000/health/live | jq
curl -fsS http://127.0.0.1:8000/health/ready | jq
./scripts/infra.sh status
```

Run the complete Linux validation sequence on a host with Docker, Node 24, npm 11, Python 3.13, Poetry, and network access to the configured package registries:

```bash
./scripts/linux/run_full_linux_validation.sh
```

Run the production Order Discovery E2E directly:

```bash
cd frontend
npx playwright test \
  tests/e2e/order-discovery-copilot-real.spec.ts \
  --config=playwright.real.config.ts
```

## Remaining production risks

1. Full dependency-backed tests and real infrastructure execution still must run on the target Linux host.
2. The reusable state/slot engine is domain-neutral, but persistence orchestration remains Order Discovery-specific. Extracting a universal persistent conversation runtime is a later architectural refactor and is not required for the current Copilot flow.
3. The local Vault setup uses one application token for practical host execution. A managed deployment should split control-plane write permission from runtime read-only identities.
4. HMAC key rotation requires a full graph re-projection before phone/email lookup is re-enabled.
5. Dynamic database credentials are not enabled; current implementation uses validated versioned Vault KV secrets with connection-pool rotation.

## Required promotion condition

Do not promote beyond **SOURCE_VALIDATED** until the target Linux host completes:

- Ruff;
- strict mypy;
- full pytest;
- frontend typecheck, lint, unit, and build;
- Compose startup and readiness;
- Vault credential validation;
- graph migration and active-release verification;
- production Copilot E2E;
- AI and data-source failure matrix;
- restart, replay, and duplicate-message checks.
