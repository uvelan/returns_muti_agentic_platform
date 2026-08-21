"""Product attributes that are not on the order line, read from the catalogue.

`source.py` projects a line from the `order_line` entity alone, and it is right
to: everything a line states about itself -- product id, description, quantity,
price, warehouse -- is on `salesLines[]`, so the read needs no join and cannot be
broken by a schema change to another entity.

**Colour is not one of those things.** The order line carries a product
description (`6X12 CEIL ALUM 4-WAY REG SAND`) and no colour field; the colour is
a property of the product, and it lives on the product catalogue keyed by master
product id. An associate confirming *which* item is coming back needs it -- "the
white one" is how the customer describes the return -- and `config/returns/
production.yaml` already declares `product_colour` as a return-detail field,
with a note saying it is unusable until a colour is actually resolvable.

**Why the binding comes from `source_resolution` and not from the active
schema.** The schema's `product` entity declares fourteen fields and none of
them is a colour, so there is nothing to read there. `source_resolution` is the
configuration system the other cross-document reads already use --
`case_order_date.py` resolves the purchase date the same way -- and it keeps the
physical path out of this module. A release that binds no colour path resolves
no colour, and a line with no colour reports `None`, which every caller renders
as an explicit unavailable state rather than as a blank.

**One query per order, not per line.** The references are collected and read
with a single `$in`; a per-line lookup on a fifty-line order would be fifty
round trips for a decoration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

__all__ = ["resolve_product_colours"]

_MISSING = object()


def _resolve(record: Any, path: Sequence[str]) -> Any:
    current = record
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _first_text(value: Any) -> str | None:
    """A colour, from a scalar or from the first entry of a list.

    `eco.colorFinish` is an array in the catalogue -- a product may state more
    than one finish -- and the first entry is the one the extract orders as
    primary. Joining several into `"White, Chrome"` would put a string on a case
    that matches no single product, so only the first is taken and the rest are
    left where they are.
    """
    if value is _MISSING or value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _first_text(item)
            if text is not None:
                return text
        return None
    rendered = str(value).strip()
    return rendered or None


async def resolve_product_colours(
    collection: AsyncCollection[dict[str, object]],
    *,
    product_references: Iterable[str],
    colour_paths: Sequence[str],
) -> dict[str, str]:
    """Master product id -> colour, for the references that have one.

    Absent keys are the answer for a product the catalogue does not hold or does
    not describe a colour for. Nothing is defaulted and nothing is guessed from
    the description: a product description containing the word "WHIT" is not a
    statement that the product is white.
    """
    references = {str(reference).strip() for reference in product_references if str(reference).strip()}
    if not references or not colour_paths:
        return {}

    paths = [tuple(path.split(".")) for path in colour_paths if path]
    colours: dict[str, str] = {}
    cursor = collection.find({"_id": {"$in": sorted(references)}})
    async for document in cursor:
        identifier = str(document.get("_id") or "").strip()
        if not identifier:
            continue
        for path in paths:
            colour = _first_text(_resolve(document, path))
            if colour is not None:
                colours[identifier] = colour
                break
    return colours
