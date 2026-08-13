"""One implementation of the statistics every backend's `profile` reports.

Written once and shared by all four connectors rather than expressed as four
dialects of aggregate SQL/aggregation-pipeline. Two reasons, and the second is the
one that matters:

* Four dialects would be four chances for `null_rate` to mean something slightly
  different, and W4.8 ranks elicitation questions by comparing these numbers
  *across* sources. A cross-source comparison of numbers computed four ways is
  not a ranking, it is a coincidence.
* The rows never leave this module. A backend that pushed the aggregation down
  would have to return values to do anything else useful with the same query;
  here the only thing a caller can obtain from `profile` is a count, and that is
  a property of the code rather than a promise.

Every statistic is a statement about the sample, never about the object. That is
why `ObjectProfile` carries `sampled_rows` and why the flags are named
`*_candidate`: with 50 rows of a million-row table, "unique in every row seen" is
evidence, not proof, and a consumer that treats it as proof has been told enough
not to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from return_platform.graph_schema_analyzer.ports.source_port import FieldProfile, ObjectProfile

__all__ = ["build_profile", "python_type_name"]

_DATETIME_TYPES = frozenset({"datetime", "date"})


def python_type_name(value: Any) -> str:
    """Map a value onto the analyzer's type vocabulary.

    `bool` is checked before `int` because bool is an int subclass in Python, and
    reporting every flag column as an integer would make it look like a candidate
    identifier with terrible selectivity rather than a flag.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def build_profile(
    *,
    source_id: str,
    object_name: str,
    rows: Sequence[Mapping[str, Any]],
    approximate_row_count: int | None,
    declared_types: Mapping[str, str] | None = None,
) -> ObjectProfile:
    """Statistics for every field observed across `rows`.

    `declared_types` comes from the backends that have a catalogue (SQL Server,
    PostgreSQL, and Neo4j's property-type procedure) and is preferred over the
    observed type, because a nullable column whose sampled values happen to all be
    null has an observed type of nothing and a declared type that is still true.

    A field absent from a document is counted as null rather than skipped. In a
    schemaless source those are the same fact to anyone deciding whether a
    property can be required, and treating absence as "no information" would
    report a 0% null rate for a field that is usually missing.
    """
    declared = dict(declared_types or {})
    field_names = list(declared)
    for row in rows:
        for key in row:
            if key not in declared and key not in field_names:
                field_names.append(key)

    total = len(rows)
    profiles: list[FieldProfile] = []
    for field_name in field_names:
        values = [row.get(field_name) for row in rows]
        non_null = [value for value in values if value is not None]
        observed_types = {python_type_name(value) for value in non_null}
        declared_type = declared.get(field_name) or _single_type(observed_types)
        distinct = _distinct_count(non_null)
        profiles.append(
            FieldProfile(
                field_name=field_name,
                declared_type=declared_type,
                null_rate=0.0 if total == 0 else (total - len(non_null)) / total,
                approximate_distinct=distinct,
                # One row makes every field trivially unique, so a single-row
                # sample would nominate the entire schema as identifiers.
                identifier_candidate=(total > 1 and len(non_null) == total and distinct == total),
                change_tracking_candidate=(
                    total > 0
                    and len(non_null) == total
                    and _is_temporal(declared_type, observed_types)
                ),
            )
        )
    return ObjectProfile(
        source_id=source_id,
        object_name=object_name,
        approximate_row_count=approximate_row_count,
        sampled_rows=total,
        fields=tuple(profiles),
    )


def _single_type(observed: set[str]) -> str:
    """`mixed` rather than a pick, and `unknown` rather than a guess.

    The analyzer's TYPE_COMPATIBILITY check treats a declared type as fact, so
    choosing one of two observed types would launder a disagreement in the data
    into a confident mapping that fails at sync time.
    """
    if not observed:
        return "unknown"
    if len(observed) == 1:
        return next(iter(observed))
    return "mixed"


def _distinct_count(values: Sequence[Any]) -> int | None:
    """`None` when the values are not hashable rather than a fabricated number.

    Mongo documents carry lists and sub-documents; counting "distinct" over them
    would need a serialisation whose equality is not the source's, and a wrong
    selectivity estimate is worse than a missing one because W4.8 would rank on
    it.
    """
    try:
        return len(set(values))
    except TypeError:
        return None


def _is_temporal(declared_type: str, observed_types: set[str]) -> bool:
    """A cursor field has to be time-ordered, so nothing but a temporal type
    qualifies -- an auto-increment integer is monotonic in most stores and in
    none of them by contract, and `SqlServerSourceScanConnector` requires a
    datetime column specifically."""
    lowered = declared_type.lower()
    if any(name in lowered for name in ("timestamp", "datetime", "date", "time")):
        return True
    return bool(observed_types) and observed_types <= _DATETIME_TYPES
