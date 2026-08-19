"""Read-only source boundary shared by Graph Schema Analyzer connectors and agent tools."""

from __future__ import annotations

import re
from enum import StrEnum


class SourceMutationRejected(ValueError):
    """Raised before a source mutation can reach a driver."""


class SourceQueryLanguage(StrEnum):
    SQL = "SQL"
    CYPHER = "CYPHER"


_SQL_READ_PREFIXES = frozenset({"SELECT", "WITH", "EXPLAIN"})
_SQL_MUTATIONS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|ALTER|DROP|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|EXEC(?:UTE)?|CALL)\b",
    re.IGNORECASE,
)
_CYPHER_MUTATIONS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV|CALL\s+db\.(?:create|drop)|CREATE\s+(?:INDEX|CONSTRAINT)|DROP\s+(?:INDEX|CONSTRAINT))\b",
    re.IGNORECASE,
)


def assert_read_only_query(query: str, language: SourceQueryLanguage) -> str:
    """Return a normalized read query or reject any source-side write capability."""
    normalized = " ".join(query.strip().split())
    if not normalized:
        raise SourceMutationRejected("A source query must not be blank.")
    if ";" in normalized.rstrip(";"):
        raise SourceMutationRejected("Multiple source statements are not allowed.")
    if language is SourceQueryLanguage.SQL:
        first = normalized.split(maxsplit=1)[0].upper()
        if first not in _SQL_READ_PREFIXES or _SQL_MUTATIONS.search(normalized):
            raise SourceMutationRejected("Graph Schema Analyzer source SQL is read-only.")
    elif _CYPHER_MUTATIONS.search(normalized):
        raise SourceMutationRejected("External graph source Cypher is read-only.")
    return normalized


def assert_mongodb_read_operation(operation: str) -> str:
    """Allow only explicitly enumerated MongoDB read operations."""
    normalized = operation.strip().casefold()
    if normalized not in {
        "find",
        "find_one",
        "aggregate_read_only",
        "list_collections",
        "list_indexes",
    }:
        raise SourceMutationRejected("Graph Schema Analyzer MongoDB sources are read-only.")
    return normalized


def assert_system_graph_target(target: str) -> None:
    """Reject agent and sync write operations aimed at anything but the system graph."""
    if target != "SYSTEM_GRAPH":
        raise SourceMutationRejected("Graph modifications may target only the system graph.")
