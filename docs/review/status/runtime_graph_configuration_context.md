# Runtime graph configuration context

Status: COMPLETE — SOURCE VERIFIED

Last updated: 2026-07-31  
Branch: `feat/v2-order-discovery-copilot`  
Commit: `11587ac feat: make agent behavior graph configurable at runtime`

## Objective

Make all non-secret agent behavior editable through versioned Neo4j configuration releases and
allow validated behavior changes to take effect without restarting API processes.

## Current architecture

Neo4j is the editable authority for three behavior domains:

- `RETURN_PLATFORM`: agent definitions, discovery, clarification, return policy, workflow,
  integrations, feature flags, and source-resolution behavior.
- `AI_GATEWAY`: task system prompts, prompt versions, provider allowlists, token limits, retry,
  rate limits, circuit breakers, and fallback behavior.
- `DEPENDENCY_SIMULATION`: dependency contracts, operation sequences, narrative settings,
  provider order, timeouts, and pricing assumptions.

The release lifecycle remains:

```text
DRAFT -> VALIDATED -> RELEASED -> SUPERSEDED -> ARCHIVED
```

Only a complete, validated `RELEASED` snapshot can become active. MongoDB stores immutable,
digest-addressed copies of all behavior domains for audit evidence. Vault remains authoritative for
secret values. Schemas, migrations, and deployment wiring remain version controlled.

## Runtime activation

- The API process publishing a release activates it immediately.
- Other API processes compare their last-good revision with the Neo4j graph head and reconcile
  within five seconds, before serving the next non-health request.
- Activation validates all domains, resolves graph-owned Vault references, rebuilds the AI route
  pool, persists the MongoDB evidence snapshot, and then swaps process state without an await point.
- If activation fails, the process continues using its last-good snapshot.
- Infrastructure endpoint changes are rejected for hot activation and require a controlled restart.
- Production and staging fail closed if any behavior domain is absent; YAML files are bootstrap and
  development recovery inputs, not production behavior authorities.

## Editing interfaces

Configuration Studio exposes one editable tab for each graph domain.

Complete replacement:

```http
PUT /data-console/v1/configuration/releases/{release_id}/domains/{domain_key}
```

Targeted RFC 7396-style merge patch:

```http
PATCH /data-console/v1/configuration/releases/{release_id}/domains/{domain_key}
Content-Type: application/json

{
  "patch": {
    "agents": {
      "order_discovery": {
        "version": "2.1"
      }
    }
  }
}
```

Every write validates the complete typed domain before updating the draft.

## Migration and rollout

Older graph releases may contain only `RETURN_PLATFORM`. Before starting the upgraded API or
workers, publish a complete three-domain release:

```bash
./scripts/prepare_runtime_configuration.sh
```

Required order:

1. Make the upgraded bootstrap command available.
2. Run `prepare_runtime_configuration.sh` while the existing application remains available.
3. Confirm the active release contains all three behavior domains.
4. Roll API and worker processes.
5. Verify `/data-console/v1/configuration/active-snapshot`.

## Verification completed

| Verification | Result | Details |
|---|---|---|
| Backend focused regression | PASS | 61 tests |
| Configuration Studio | PASS | 3 tests |
| Ruff | PASS | Changed backend source and tests |
| Mypy | PASS | 16 affected source/test files |
| Frontend ESLint | PASS | Configuration Studio and mocks |
| OpenAPI generation | PASS | Backend, frontend, and root canonical contracts synchronized |
| Whitespace validation | PASS | `git diff --check` |

Docker Compose and live dependency validation were not run for this work package. The earlier
attempt to exercise Docker was blocked by the local Docker engine/API, so this status is
source-verified rather than production-validated.

## Important files

- `backend/src/return_platform/configuration/runtime_activation.py`
- `backend/src/return_platform/configuration/snapshot.py`
- `backend/src/return_platform/data_console/api/configuration.py`
- `backend/src/return_platform/configuration/cli/bootstrap_graph_configuration.py`
- `backend/config/ai_gateway.yaml`
- `frontend/src/features/data-console/pages/settings/ConfigurationStudioPage.tsx`
- `backend/tests/test_configuration_api.py`
- `backend/tests/test_graph_configuration.py`

## Next action

Run the migration and Docker-backed validation in an environment with Neo4j, MongoDB, Vault, and
the API stack available. Confirm that a prompt-only release changes subsequent AI traces without a
restart and that a second API replica reconciles the same graph revision within five seconds.

Screenshots: DEFERRED  
Git commit created: YES — `11587ac`
