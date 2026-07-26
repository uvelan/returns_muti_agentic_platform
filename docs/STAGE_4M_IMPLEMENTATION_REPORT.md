# Stage 4M — Implementation Report

## Implemented

Stage 4M extends the production return application with configurable development/test service emulators for OMC, Parcel, Freight/TMS and LSI.

### Deterministic external service behavior

- OMC RMA, legacy return, readback, status, customer resolution, product resolution, downstream RGA and vendor credit.
- Parcel label, void, reissue, tracking, carrier acceptance and exceptions.
- Freight quote, approval, BOL, tender, booking, appointment, carrier arrival, pickup, tracking, failure and reschedule.
- LSI authorization acknowledgment, receipt, license plate, disposition, warehouse completion, lot, RGA, vendor debit, vendor credit and recovery closure.
- Idempotent identifiers and operation records.
- State prerequisites for parcel, freight and LSI operations.
- Correct separation of RMA and downstream RGA.

### Lightweight AI and guaranteed fallback

The simulator uses the configured lightweight model order:

```text
GOOGLE  gemini-3.1-flash-lite
NVIDIA  meta/llama-3.2-3b-instruct
```

AI is called only after the deterministic result exists. It may return only:

```text
message
summary
nextAction
```

Timeouts, missing credentials, provider errors, unexpected exceptions, invalid JSON and invalid response schemas all produce the versioned default template. The deterministic operation remains unchanged.

### AI metrics

Every attempt or final template response records:

```text
provider
model
status
attempt
latency
input tokens
output tokens
total tokens
fallback usage
configured cost estimate
request digest
response digest
error code
operation/session/dependency correlation
```

Aggregate metrics are available by provider, model, dependency and operation. Pricing defaults to zero until approved current prices are entered.

### Dedicated UI

The implementation uses separate pages for overview, OMC, Parcel, Freight/TMS, LSI, AI metrics and operation detail. No single-page combined simulator was introduced.

### Runtime scripts

```bash
cp .env.example .env
./scripts/bootstrap_host.sh
./scripts/start_stage4m_simulation.sh

./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL
./scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY

./scripts/run_stage4m_gates.sh
```

## Validation completed in the packaging environment

- Python compilation passed.
- 14 focused dependency-simulator and production-state tests passed.
- Stage 4M deterministic lifecycle validation passed.
- Frontend syntax validation passed for 162 TypeScript/TSX files.
- Stage 4 source validation passed.
- Stage 4 source-contract validation passed.
- Stage 4M shell scripts passed `bash -n`.

## Validation not completed in the packaging environment

The environment did not provide Docker, the repository's complete Python dependency environment, Ruff, mypy, Node 24/npm 11, or installed frontend dependencies. Therefore the following remain required in the supported Linux environment:

- full pytest suite;
- Ruff and Ruff format check;
- strict mypy;
- frontend lint/typecheck/unit/build;
- Playwright and accessibility tests;
- Docker/Compose live startup;
- live Temporal/Mongo restart validation;
- real Google/NVIDIA model calls;
- real external adapters.

## Classification

```text
SOURCE_VALIDATED
LIVE_STACK_VALIDATION_PENDING
```
