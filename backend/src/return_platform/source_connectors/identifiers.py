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


def split_qualified_name(value: str, *, default_namespace: str) -> tuple[str, str]:
    """Split `schema.table` into validated parts, defaulting an absent schema.

    Source inspection addresses every object by one opaque name so that the
    analyzer's scope check is an exact string comparison (see
    `graph_schema_analyzer/domain/source_scope.py`). SQL backends then have to
    take that name apart again, and doing it here means both parts go through
    `validate_identifier` on every path -- a caller that split the string itself
    and validated only the table is how a schema name reaches bracketed SQL
    unchecked.

    A name with more than one dot is refused rather than split from the right: a
    three-part `database.schema.table` names a database this connection was not
    opened against, and silently reading the last two parts would quietly ignore
    the first.
    """
    parts = value.split(".")
    if len(parts) == 1:
        namespace, name = default_namespace, parts[0]
    elif len(parts) == 2:
        namespace, name = parts[0], parts[1]
    else:
        raise UnsafeIdentifierError(
            f"object name {value!r} is not `name` or `namespace.name`; "
            "cross-database references are not addressable through this connection"
        )
    return (
        validate_identifier(namespace, what="schema"),
        validate_identifier(name, what="table"),
    )
