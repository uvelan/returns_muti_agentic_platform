"""What a selected line *is*, for a reader who is not looking at the order.

`SelectedItemProjection` carries a line reference, a product reference, a
quantity, a reason and a condition. Every one of those is an identifier or a
choice; none of them is a name. A Support handoff built from the selection alone
therefore says "line 1, product 4000096, quantity 1" -- which is true, and is
useless to the person who has to find the item.

This resolves the missing half from the same two sources the order-lines screen
already reads: the sales document for the line's own description and SKU, and
the product catalogue for the colour, which is not on the line at all. Both are
read through the bindings the active release declares, so nothing here has to be
edited when a field is re-bound.

It is a **port on the workflow activities**, not a call inside them, for the
reason `bay_placement` is: the activity's dependencies are the surface a reader
checks to know what it can reach, and a schema-driven source read acquired
quietly inside a method is a dependency nobody declared.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pymongo.asynchronous.collection import AsyncCollection

from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.order_lines.product_attributes import resolve_product_colours
from return_platform.operations.order_lines.source import load_source_order_lines

__all__ = [
    "CaseOrderLineDetail",
    "OrderLineDetailPort",
    "SourceOrderLineDetails",
]

logger = logging.getLogger("return_platform.operations.order_lines.case_detail")


@dataclass(frozen=True, slots=True)
class CaseOrderLineDetail:
    """One line, named. Every field nullable and none defaulted."""

    line_reference: str
    sku: str | None = None
    description: str | None = None
    colour: str | None = None
    product_reference: str | None = None


class OrderLineDetailPort(Protocol):
    """Line reference -> detail, for one confirmed order."""

    async def line_details(self, order_reference: str) -> Mapping[str, CaseOrderLineDetail]: ...


class SourceOrderLineDetails:
    """The port, over the sales source and the product catalogue.

    Best-effort in one direction only: an unreadable source answers with nothing
    and the handoff renders those fields as unavailable. It never answers with a
    *partial* line -- a description resolved and a colour silently dropped is
    indistinguishable from a product that has no colour.
    """

    def __init__(
        self,
        *,
        sales: AsyncCollection[dict[str, object]],
        catalogue: AsyncCollection[dict[str, object]],
        schema: ActiveSchema,
        colour_paths: Sequence[str],
    ) -> None:
        self._sales = sales
        self._catalogue = catalogue
        self._schema = schema
        self._colour_paths = tuple(colour_paths)

    async def line_details(self, order_reference: str) -> Mapping[str, CaseOrderLineDetail]:
        try:
            lines = await load_source_order_lines(
                self._sales, schema=self._schema, order_reference=order_reference
            )
        except Exception:  # noqa: BLE001 - see the class docstring
            logger.warning(
                "case_order_line_detail_unavailable",
                extra={"order_reference": order_reference},
                exc_info=True,
            )
            return {}
        if not lines:
            return {}

        colours: Mapping[str, str] = {}
        if self._colour_paths:
            try:
                colours = await resolve_product_colours(
                    self._catalogue,
                    product_references=[line.product_reference or "" for line in lines],
                    colour_paths=self._colour_paths,
                )
            except Exception:  # noqa: BLE001 - a colour is not worth losing the names over
                logger.warning(
                    "case_order_line_colour_unavailable",
                    extra={"order_reference": order_reference},
                    exc_info=True,
                )

        return {
            line.line_reference: CaseOrderLineDetail(
                line_reference=line.line_reference,
                sku=line.sku,
                description=line.description,
                colour=colours.get(line.product_reference or ""),
                product_reference=line.product_reference,
            )
            for line in lines
        }
