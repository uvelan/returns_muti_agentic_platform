"""Shared SQL identifier validation for embedding names in bracketed SQL.

Extracted from `dynamic_knowledge.connectors.sqlserver` (Phase 8 / Wave C1) --
`data_console.api.browser`'s admin preview endpoint had its own, separately
maintained copy of the same pattern; both now share this one.
"""

from __future__ import annotations

import re

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeIdentifierError(ValueError):
    """A name failed safe-identifier validation before being embedded in SQL."""


def validate_identifier(value: str, *, what: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise UnsafeIdentifierError(f"unsafe SQL Server {what}: {value!r}")
    return value
