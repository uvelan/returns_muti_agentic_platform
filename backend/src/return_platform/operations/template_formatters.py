"""The fixed, code-side formatter allowlist for support templates (contracts.md sect. 8).

A template field names a formatter by id; the ids live here and only here, so
the configuration model can refuse an unknown id at release validation and the
renderer can never be handed one. There is deliberately no registration
surface: a formatter is code with tests, not configuration, and the whole
point of the allowlist is that publishing a release cannot introduce
behaviour this module does not already contain.

Every formatter takes whatever the binding resolved and returns text, or
raises `TemplateFormatterError` when the value is not something it can
honestly render -- the renderer turns that into a field-level failure (and a
`TemplateGap` when the field is required) rather than inventing output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

__all__ = [
    "FORMATTER_IDS",
    "TemplateFormatterError",
    "UNAVAILABLE",
    "format_value",
]

#: One spelling for an absent value, shared with `compose_support_handoff` --
#: a reader learns it once, and it is deliberately not an empty string.
UNAVAILABLE: Final[str] = "Not available"


class TemplateFormatterError(ValueError):
    """A value the named formatter cannot honestly render."""


def _text(value: Any) -> str:
    if value is None:
        raise TemplateFormatterError("no value to format")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (Mapping, list, tuple, set)):
        raise TemplateFormatterError("structured value given to a scalar formatter")
    text = str(value).strip()
    if not text:
        raise TemplateFormatterError("blank value")
    return text


def _date(value: Any) -> str:
    """An instant or date, rendered as ISO-8601 -- the spelling the handoff uses."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = _text(value)
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError as invalid:
        raise TemplateFormatterError(f"not a date: {text!r}") from invalid


def _currency(value: Any) -> str:
    """An amount with two decimal places. The unit travels in the label."""
    if isinstance(value, bool) or value is None:
        raise TemplateFormatterError("not an amount")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as invalid:
        raise TemplateFormatterError(f"not an amount: {value!r}") from invalid
    return f"{amount:,.2f}"


#: The address parts, in rendering order. A mapping renders only the parts it
#: carries; nothing absent is invented.
_ADDRESS_PARTS: Final = ("line1", "line2", "city", "region", "postal_code", "country")


def _address(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = [str(value[part]).strip() for part in _ADDRESS_PARTS if value.get(part)]
        if not parts:
            raise TemplateFormatterError("address mapping carries no parts")
        return ", ".join(parts)
    return _text(value)


#: The per-item lines, in the order and spelling `compose_support_handoff`
#: prints them -- the default variant reproduces today's output verbatim.
_ITEM_LINES: Final = (
    ("Product Name", "productName"),
    ("Colour", "colour"),
    ("SKU", "sku"),
    ("Confirmed Return Quantity", "quantity"),
    ("Return Reason", "reason"),
    ("Product Condition", "condition"),
)


def _item_value(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if value is None:
        return UNAVAILABLE
    text = str(value).strip()
    return text or UNAVAILABLE


def _item_list(value: Any) -> str:
    """The selected-lines block, one entry per item, absent values named as such.

    Accepts a sequence of item mappings (`lineReference` plus the catalogue
    above). An empty or absent list renders the same single line the handoff
    renders, because "no lines" is a statement, not a rendering failure.
    """
    if value is None:
        return f"- Selected lines: {UNAVAILABLE}"
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TemplateFormatterError("item_list expects a sequence of items")
    if not value:
        return f"- Selected lines: {UNAVAILABLE}"
    lines: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TemplateFormatterError("item_list expects mappings")
        reference = _item_value(item, "lineReference")
        lines.append(f"- Line/Order-Line Number: {reference}")
        for label, key in _ITEM_LINES:
            lines.append(f"  - {label}: {_item_value(item, key)}")
    return "\n".join(lines)


_FORMATTERS: Final = {
    "text": _text,
    "date": _date,
    "currency": _currency,
    "address": _address,
    "item_list": _item_list,
}

#: The allowlist the configuration model validates against. Frozen: adding an
#: id is a code change here, never a configuration change.
FORMATTER_IDS: Final[frozenset[str]] = frozenset(_FORMATTERS)


def format_value(formatter_id: str, value: Any) -> str:
    """Render `value` under the named formatter, or refuse."""
    formatter = _FORMATTERS.get(formatter_id)
    if formatter is None:
        raise TemplateFormatterError(f"unknown formatter: {formatter_id}")
    return formatter(value)
