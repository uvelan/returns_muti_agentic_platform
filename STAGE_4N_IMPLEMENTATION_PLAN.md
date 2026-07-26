# Stage 4N — AI Gateway Hardening Implementation Plan

## Goal

Make AI usage resilient, measurable, task-bound, domain-safe, and non-blocking while preserving deterministic business authority.

## Delivery order

### 1. Configuration and migration compatibility

- Accept credential lists for each provider.
- Accept lightweight and standard model lists.
- Preserve legacy single-value settings temporarily.
- Validate immutable task policies from YAML.

### 2. Route pool

- Expand provider/model/key combinations.
- Assign safe credential and route IDs.
- Add task tier filtering.
- Add application/tier/provider/model/credential/route limits.
- Add concurrency controls and circuit breakers.
- Add key, model, and provider failover.

### 3. Safety boundary

- Add exact per-task input allowlists.
- Detect prompt injection, secret requests, role bypass, direct SQL, and unauthorized business actions.
- Reject unrelated requests.
- Restrict custom prompts to development/test.
- Validate exact output schemas.

### 4. Metrics

- Persist every route attempt.
- Capture token, latency, model, credential, route, tier, failure, fallback, and safety evidence.
- Add route health, task registry, attempt metrics, and summary APIs.

### 5. Dependency simulator integration

- Route simulator narratives through the central lightweight pool.
- Prohibit tier escalation.
- Use deterministic fallback for any AI failure.
- Ensure deterministic OMC/parcel/freight/LSI operations never depend on AI.

### 6. Dedicated UI

- Route health page.
- Task policies page.
- Usage metrics page.
- Safety-test page.
- Existing request and dependency simulator AI pages remain separate.

### 7. Validation and operations

- Add focused routing/safety/simulator tests.
- Add dependency-light simulator AI E2E.
- Add live-stack E2E wrapper.
- Add source gate wrapper and evidence.
- Update README and runbooks.

## Exit criteria

- Key and model list expansion proven.
- Lightweight and standard isolation proven.
- Credential and model rotation proven.
- Prompt injection and out-of-domain requests blocked.
- Exact output schemas enforced.
- Successful AI usage metrics proven.
- AI outage and invalid response fallback proven.
- Dependency simulation operation completes despite AI failure.
- Dedicated pages and commands documented.
