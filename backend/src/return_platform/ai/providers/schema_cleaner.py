"""Best-effort JSON-schema simplification for Gemini's structured-output support."""

from __future__ import annotations

import copy
from typing import Any

#: Keys Gemini's structured-output parser rejects. Dropped rather than
#: translated: they carry no constraint, only documentation and defaults.
_UNSUPPORTED_KEYS = ("title", "default", "additionalProperties")


def _merge_under(node: dict[str, Any], resolved: Any) -> None:
    """Fill `node` from an inlined definition without overwriting what it states.

    Both inlining paths used to let the *referenced* definition win: `$ref`
    returned the definition and dropped every sibling key outright, and `anyOf`
    called `node.update(resolved)`. The key they most often share is
    `description`, and pydantic puts the two descriptions in exactly those two
    places -- a field's `Field(description=...)` on the referencing node, the
    referenced model's docstring on the `$def`.

    So a field carrying documentation lost it here, before any provider saw the
    schema. `AgentAction.action_type` is the case that mattered: it is a `$ref`
    to an enum with a sibling `description` stating which payload each action
    type requires, and that sentence was deleted on the way to the model. The
    field's own annotation is the more specific of the two and is the one to
    keep; the definition supplies only what the field did not say for itself.
    """
    if not isinstance(resolved, dict):
        return
    for key, value in resolved.items():
        if key not in node:
            node[key] = value


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
            node.pop("$ref")
            _merge_under(node, _resolve(copy.deepcopy(defs[ref_name]), defs))

    if "anyOf" in node:
        # Gemini has no union type. Take the first non-null branch and merge it
        # in place, which is how an `X | None` field becomes a plain optional X.
        for item in node.pop("anyOf"):
            if isinstance(item, dict) and item.get("type") != "null":
                _merge_under(node, _resolve(item, defs))
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
