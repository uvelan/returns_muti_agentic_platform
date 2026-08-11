"""Shared helpers for reading generated records.

Generated records are real documents with real nesting, because the active
schema reads through that nesting -- `order_line` explodes `salesLines[]` and
reads `lineData.*`, `contact_point` explodes `customer.address[]`. The schema
registry, meanwhile, names fields by dotted path. Tests that indexed a record
with `record["salesHdr.salesHdrData.custId"]` were relying on the seed being
flat, which is precisely the shape that made every nested field extract as
null.

`at` and `leaf_paths` bridge the two: address a document by the path the
registry declares, without assuming the document is flat.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def at(record: dict[str, Any], path: str) -> Any:
    """Read a dotted registry path out of a (possibly nested) record.

    Stops at an exact key match first, so a declared field whose own name
    contains a dot -- `customer.address`, whose value is an array -- resolves as
    itself rather than being walked into.
    """
    if path in record:
        return record[path]
    node: Any = record
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            raise KeyError(f"{path!r} is not present in the record")
        node = node[segment]
    return node


def leaf_paths(record: dict[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar in a record, keyed by its dotted path.

    Lists are yielded whole rather than descended: an array is a value in this
    schema, not a level, and the callers that use this are checking scalars.
    """
    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            yield from leaf_paths(value, path)
        else:
            yield path, value
