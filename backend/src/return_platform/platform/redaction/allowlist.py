"""Allowlist-based Redactor implementation."""

from __future__ import annotations

from collections.abc import Mapping


class AllowlistRedactor:
    """Keeps only the configured field names; drops everything else.

    Fail-closed by construction: a field is never emitted unless it was explicitly
    allowlisted, so a newly added payload field is redacted by default until someone
    deliberately allowlists it.
    """

    def __init__(self, allowed_fields: frozenset[str]) -> None:
        self._allowed_fields = allowed_fields

    def redact(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return {key: value for key, value in payload.items() if key in self._allowed_fields}
