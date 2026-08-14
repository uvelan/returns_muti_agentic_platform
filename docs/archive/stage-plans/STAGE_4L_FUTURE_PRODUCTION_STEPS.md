# Stage 4L — Future Production Steps

**Starting point:** Stage 4L source implementation  
**Current classification:** `SOURCE_VALIDATED`

## 1. Restore the supported toolchain

Use:

- Python 3.13;
- Poetry with the committed `pyproject.toml`;
- Node 24;
- npm 11;
- Docker/Compose for infrastructure.

Run all dependency-backed gates:

```bash
cd backend
poetry install
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests
poetry run pytest -q

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
npm run test:a11y
npm run contracts:check
```

Do not modify production behavior merely to bypass these gates. Fix every issue at its owning boundary.

## 2. Regenerate OpenAPI contracts

The new production APIs must be exported and the generated TypeScript client updated:

```bash
cd frontend
npm run contracts:generate
npm run contracts:check
```

Review every generated operation and remove any hand-maintained duplicate contract that can safely be replaced by the generated client.

## 3. MongoDB and Temporal durability validation

Start the full infrastructure and validate:

- all new indexes;
- transaction behavior for Support work-item creation;
- optimistic concurrency;
- outbox lease recovery;
- worker restart;
- signal idempotency;
- Temporal replay safety;
- legacy and production workflow isolation;
- continuation after API and worker restarts.

Required evidence level: `SANDBOX_VALIDATED`.

## 4. OMC contract implementation

Obtain approved procedures, APIs, or integration services for:

- v1 return creation;
- v2 RMA/cart/cartItem creation;
- reason/fault/return-method/customer-resolution changes;
- freight quote and BOL creation;
- authoritative readback.

Implement the `OmcReturnCommandGateway` adapter. Do not add generic OMC tables or direct agent SQL.

Validate:

- idempotent command retry after timeout;
- same key/different digest conflict;
- readback mismatch handling;
- v1/v2 routing;
- RMA versus RGA semantics;
- no duplicate return creation.

## 5. Carrier integration

Implement an approved carrier/P44/Convey/MercuryGate adapter for:

- quote retrieval;
- carrier selection;
- BOL tender;
- booking confirmation where available;
- appointment changes;
- pickup evidence;
- tracking events.

Preserve the state distinction:

```text
REQUESTED → TENDERED → BOOKED → ARRIVED → PICKED_UP → RECEIVED
```

Never infer booking or pickup from tendering.

## 6. External ticketing and notification

Keep the internal Returns Support work item authoritative. Add optional mirrors for Teams, ServiceNow, Jira, email, or another approved system through the outbox.

Requirements:

- external reference attached after authoritative acknowledgment;
- delivery retry and dead-letter handling;
- inbound reply reconciliation;
- duplicate-event prevention;
- PII-safe payloads;
- internal workflow remains available during external outages.

## 7. LSI receipt and vendor recovery

Implement file/API ingestion for:

- LSI authorization and receipt files;
- license plates;
- product resolutions;
- RGA request/debit files;
- lot shipment;
- vendor credit memo.

Customer-return completion and vendor-recovery closure must remain separate.

## 8. Warehouse authority decision

Confirm whether LSI or another warehouse system owns physical bay/location. Then select one mode:

```text
PLATFORM_AUTHORITATIVE
EXTERNAL_LSI
PROJECTION_ONLY
```

Disable Platform SQL mutations if an external system is authoritative.

## 9. Complete production E2E matrix

Validate at least:

1. v2 LSI parcel return;
2. v1 direct-vendor return;
3. branch LTL;
4. offsite heavy pickup;
5. offsite parcel;
6. no-physical-return;
7. split shipment/multiple handling units;
8. duplicate command retry;
9. BOL tender without booking;
10. failed/no-show pickup and reschedule;
11. LSI receipt/license plate;
12. downstream RGA/vendor credit;
13. dependency outage/recovery;
14. worker/API restart;
15. permission and concurrency failures.

Each scenario must persist evidence and prove no fabricated state transition.

## 10. Security and governance

Complete:

- production identity provider integration;
- least-privilege role matrix;
- PII classification and masking;
- artifact malware/content scanning;
- retention and deletion policy;
- secrets manager integration;
- audit immutability;
- outbound payload allowlists;
- dependency and container scanning;
- signed build provenance and SBOM.

## 11. OCR and image-processing extension

Do this only after the return E2E is stable. Keep the existing artifact contracts and add asynchronous workers for:

- receipt/invoice OCR;
- model/SKU extraction;
- damage-image classification;
- duplicate-image detection;
- packaging-condition analysis;
- confidence and human-review routing.

AI output must remain advisory. Original artifacts, hashes, extraction version, confidence, and reviewer decisions must be retained.

## 12. Advanced agent capabilities

After production flows are stable, consider:

- governed policy retrieval;
- tool-call interception in non-production environments;
- approved NCR workflow;
- vendor-recovery recommendations;
- graph-based process improvement;
- cost/token reporting;
- policy simulation and promotion gates.

Do not introduce dynamic workflow composition or autonomous capability execution until deterministic controls and evaluation evidence exist.

## 13. Promotion criteria

### `CONTRACT_TESTED`

- Ruff, strict mypy, full pytest pass;
- frontend lint/typecheck/test/build pass;
- no OpenAPI drift.

### `SANDBOX_VALIDATED`

- all infrastructure healthy;
- production workflow survives restarts;
- OMC/carrier/ticket sandbox adapters pass;
- full E2E and failure matrix passes.

### `PRODUCTION_READY`

- security, performance, observability, DR, deployment, and rollback evidence complete.

### `PRODUCTION_VALIDATED`

- protected production deployment;
- smoke tests;
- SLO monitoring;
- rollback proof;
- business-owner acceptance.
