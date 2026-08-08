"""Structured-logging AuditSink implementation."""

from __future__ import annotations

import logging

from return_platform.platform.contracts.audit import AuditEvent

logger = logging.getLogger("return_platform.audit")


class LoggingAuditSink:
    """Emits one structured log record per AuditEvent.

    A durable, queryable audit store (SystemStore-backed) is a separate future
    concern; this implementation is deliberately the simplest one that is still
    correct today -- every audit event reaches the log pipeline, in order, with a
    stable field schema, and never raises back into the caller's own operation.
    """

    async def record(self, event: AuditEvent) -> None:
        logger.info(
            "audit_event",
            extra={
                "audit_event_type": event.event_type,
                "audit_occurred_at": event.occurred_at.isoformat(),
                "audit_correlation_id": event.correlation_id,
                "audit_principal_id": event.principal_id,
                "audit_payload": dict(event.payload),
            },
        )
