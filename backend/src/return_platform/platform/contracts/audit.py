"""Platform-neutral audit trail contract.

An AuditEvent records that a business-significant action happened; it never carries
raw secrets or unredacted personal data -- callers pass already-redacted payloads
(see platform.contracts.redaction.Redactor).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    occurred_at: datetime
    correlation_id: str
    principal_id: str
    payload: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class AuditSink(Protocol):
    """Durably records AuditEvents. Never raises to protect the caller's own operation --
    an audit failure must not roll back or block the business action it is recording."""

    async def record(self, event: AuditEvent) -> None: ...
