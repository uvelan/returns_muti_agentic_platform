"""Best-effort JSON-schema simplification for Gemini's structured-output support."""

from __future__ import annotations

import copy
from typing import Any

#: Keys Gemini's structured-output parser rejects. Dropped rather than
#: translated: they carry no constraint, only documentation and defaults.
_UNSUPPORTED_KEYS = ("title", "default", "additionalProperties")


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Inline `$ref`s and flatten `anyOf`, recursively.

    Split out of `clean_gemini_schema` and given types. It was a closure over
    `defs` annotated with nothing at all, which made every one of its four
    recursive calls an untyped call and its return `Any` -- so a caller passing
    the result somewhere typed got no checking whatsoever.
    """
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        # Format is "#/$defs/Name"; the last segment is the definition key.
        ref_name = str(node["$ref"]).split("/")[-1]
        if ref_name in defs:
            return _resolve(copy.deepcopy(defs[ref_name]), defs)

    if "anyOf" in node:
        # Gemini has no union type. Take the first non-null branch and merge it
        # in place, which is how an `X | None` field becomes a plain optional X.
        for item in node.pop("anyOf"):
            if isinstance(item, dict) and item.get("type") != "null":
                resolved = _resolve(item, defs)
                if isinstance(resolved, dict):
                    node.update(resolved)
                break

    for key in _UNSUPPORTED_KEYS:
        node.pop(key, None)

    return {key: _resolve(value, defs) for key, value in node.items()}


def clean_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema_copy = copy.deepcopy(schema)
    defs: dict[str, Any] = schema_copy.pop("$defs", {})
    resolved = _resolve(schema_copy, defs)
    # `_resolve` returns `Any` because a schema node can be any JSON value; at
    # the top level it is a dict by construction, and saying so is what lets
    # callers of this function get checked.
    return resolved if isinstance(resolved, dict) else schema_copy
