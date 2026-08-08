# platform/audit

Records that a business-significant action happened, without ever carrying raw
secrets or unredacted personal data.

`AuditSink.record(event)` never raises back into the caller — a failure to record an
audit event must never roll back or block the business action it describes. Callers
are responsible for redacting `payload` (see `platform/redaction`) before constructing
the `AuditEvent`; this package has no opinion about what should be redacted.

`LoggingAuditSink` is the only implementation today: one structured log record per
event, at INFO, with a stable field schema (`audit_event_type`, `audit_occurred_at`,
`audit_correlation_id`, `audit_principal_id`, `audit_payload`). A durable, queryable
SystemStore-backed audit store is a separate future concern — introduce it as a second
`AuditSink` implementation behind the same contract, not by changing the contract.
