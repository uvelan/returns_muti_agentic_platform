"""Value redaction at the provider boundary.

`AIGatewayService._redact_and_validate` rejects a *top-level payload key* whose
name looks sensitive. That is the right rule for the eligibility task, whose
payload is a flat dict of business fields, and it is no rule at all for the
Order Agent, whose payload is five keys of which one -- `contextJson` -- is a
JSON *string* holding the transcript and every graph row the agent retrieved.
Customer names, addresses, phones and emails travelled to the provider inside
it, and the interception store recorded the same content.

So redaction has to happen where the request is actually built, and it has to
follow the data: into dicts, into lists, and into strings that are themselves
JSON.

**What is masked, precisely.** A *scalar* under a sensitive key. Not a dict, not
a list. That distinction is what keeps this from breaking the agent: in
`compact_schema`, `customer_name` is a key whose value is a metadata object
(`{"description": ..., "searchable": true, ...}`) describing a field the agent
is allowed to search; in a graph row, `customer_name` is a key whose value is
the string "Jane Doe". Blanking both would strip the schema the agent reasons
over and leave it unable to plan a query at all. Recursing into containers and
masking only leaf values masks the data and preserves the description of it.

The masked form keeps the value's length class rather than its content, because
"there is a phone number here" is information the model legitimately needs to
decide whether it already has an anchor, and "555-0100" is information it does
not.

**Which names count as sensitive is not decided here.** That list moved to
`platform.redaction.sensitive_keys` when W4.6 needed the same answer for analyzer
samples, which cannot import `ai` (design doc 2.7). This module kept only the
part that is specific to a provider payload: recursion into a JSON-encoded
string, and the constant that replaces a leaf. A sample gets a different
replacement for reasons `platform.redaction.sample_masking` sets out; both ask
`is_sensitive_key` the question.
"""

from __future__ import annotations

import json
from typing import Any, Final

from return_platform.platform.redaction.sensitive_keys import is_sensitive_key

__all__ = ["REDACTED", "redact_payload"]

REDACTED: Final = "[REDACTED]"

# Depth is bounded so a hostile or malformed payload cannot drive unbounded
# recursion. Deeper than the gateway's own nesting limit (4) because a nested
# JSON-encoded string resets the apparent depth without resetting the real one.
_MAX_DEPTH: Final = 12


def _redact_json_string(value: str, depth: int) -> str:
    """Recurse into a string that is itself JSON, else return it unchanged.

    This is the whole point of the module: `contextJson` is a string to the
    payload validator and a document to everyone else.
    """
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        decoded = json.loads(stripped)
    except (TypeError, ValueError):
        return value
    if not isinstance(decoded, (dict, list)):
        return value
    return json.dumps(
        _redact(decoded, depth + 1),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _redact(value: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text) and not isinstance(nested, (dict, list)):
                # A leaf under a sensitive key. `None` stays `None`: "absent" is
                # not sensitive, and masking it would tell the model a value
                # exists where none does.
                result[key_text] = REDACTED if nested is not None else None
                continue
            result[key_text] = _redact(nested, depth + 1)
        return result
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _redact_json_string(value, depth)
    return value


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload as it may leave the platform.

    Applied at `ProviderRequest` construction rather than at the API edge so
    every path reaches it -- including `structured_invocation`, which had no
    redaction of any kind and is the path the Order Agent actually uses.
    """
    redacted = _redact(payload, 0)
    # `_redact` preserves shape, so a dict in is a dict out; assert rather than
    # cast so a future change to `_redact` cannot silently return the wrong type.
    assert isinstance(redacted, dict)
    return redacted
