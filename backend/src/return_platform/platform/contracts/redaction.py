"""Platform-neutral payload redaction contract.

Distinct from secrets.vault.SecretRedactor, which scrubs secret *values* out of log
lines. A Redactor scrubs whole *fields* out of a structured payload (agent context,
audit records, checkpoint state) before it is persisted or emitted, based on an
allowlist decided by the caller -- it has no notion of what a secret looks like.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class Redactor(Protocol):
    """Returns a copy of payload containing only allowlisted fields."""

    def redact(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...
