"""The order's lines, read from the source document the graph is built from.

**No physical path is written in this module.** Every one of them -- which array
holds the lines, where the line number lives, which key carries the SKU -- comes
off the `order_line` entity in the active schema, the same declaration
`GraphSyncService` projects the `OrderLine` nodes from. A literal here would be
correct only until the next release re-binds a field, and the failure would be
silent: a line that projected `null` for a description nobody could explain.

**Why not `GenericSourceRecordExtractor`.** That extractor is the platform's one
schema-driven exploder and it is exactly right for a sync: it walks *every*
entity bound to a source asset and returns mutations for all of them. Four
entities are bound to `source_sales`, and one of them (`contact_point`) carries
a derived field. An API read that had to succeed at extracting a customer's
contact points before it could show an associate the lines of their order would
fail for reasons that have nothing to do with the order -- and today it would
also need an HMAC key it has no business holding. This walks one entity, reads
no derived field, and cannot be broken by a schema change to another entity.

**Why the source document and not the graph.** The lines are self-contained on
`salesLines[]` -- product id, description, ordered quantity, unit price and
warehouse are all on the line -- so the read needs no traversal and no product
catalogue. (The catalogue would not help: it holds one document against 482
distinct product ids, so a join to `product` resolves for essentially nothing.)
The sales collection is uniquely indexed on the order number, which makes this
one indexed `find_one` rather than a graph query whose generation scoping and
guard policy would have to be reasoned about for a read the case already
authorized.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from pymongo.asynchronous.collection import AsyncCollection

from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDefinition,
    PathOrigin,
)

__all__ = [
    "ORDER_LINE_ENTITY_ID",
    "SALES_ORDER_NUMBER_FIELD",
    "SourceOrderLine",
    "load_source_order_lines",
    "project_source_order_lines",
]

#: The entity whose definition drives this whole module.
ORDER_LINE_ENTITY_ID: Final = "order_line"

#: The field on that entity whose `physical_path` is the order number. Used to
#: build the `find_one` filter, so the query is bound to the same declaration
#: the projection is -- a rebinding moves both together or neither.
SALES_ORDER_NUMBER_FIELD: Final = "sales_order_number"

_LINE_NUMBER_FIELD: Final = "line_number"
_SKU_FIELD: Final = "sku"
_DESCRIPTION_FIELD: Final = "product_description"
_ORDERED_QUANTITY_FIELD: Final = "ordered_quantity"
_NET_PRICE_FIELD: Final = "net_price"
_MASTER_PRODUCT_FIELD: Final = "master_product_id"
_PRODUCT_FIELD: Final = "product_id"

_MISSING: Final = object()


class OrderLineSchemaUnavailableError(RuntimeError):
    """The active schema does not declare the entity this read is built on.

    Raised rather than defaulted. A release that has dropped `order_line` cannot
    be read for order lines, and answering with an empty list would present a
    configuration fault as an order that has nothing on it.
    """


@dataclass(frozen=True, slots=True)
class SourceOrderLine:
    """One line of the confirmed order, as the source states it.

    Every field except `lineReference` is nullable, and none of them is
    defaulted. A source that does not carry a unit price is reported as carrying
    no unit price; substituting a zero would put a number on a refund basis that
    nobody published.
    """

    #: The line part of the natural key -- what `return_item.order_line_reference`
    #: is documented to carry. The account and the order number are supplied by
    #: the case, which is the authorization boundary the read is scoped to.
    line_reference: str
    sku: str | None
    description: str | None
    ordered_quantity: int | None
    #: Unit selling price, as `Decimal`, never `float`. A refund basis computed
    #: through binary floating point is a refund basis that disagrees with the
    #: invoice by a cent on some orders and not others.
    unit_price: Decimal | None
    product_reference: str | None


def _resolve(record: Any, path: Sequence[str]) -> Any:
    current = record
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _text(value: Any) -> str | None:
    if value is _MISSING or value is None or isinstance(value, bool):
        return None
    rendered = str(value).strip()
    return rendered or None


def _whole(value: Any) -> int | None:
    """A quantity, or `None`. Never a rounded float and never a coerced string.

    `float` is accepted only when it is integral: ERP extracts write `3.0` for
    three, and refusing that would report a quantity the invoice plainly states.
    A genuinely fractional quantity is not a returnable unit count, so it is
    reported as unknown rather than truncated into one.
    """
    if value is _MISSING or value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else None
    return None


def _money(value: Any) -> Decimal | None:
    if value is _MISSING or value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _field_value(
    entity: EntityDefinition,
    field_id: str,
    *,
    root: Mapping[str, Any],
    record: Mapping[str, Any],
) -> Any:
    """One declared field, read from whichever record its `path_origin` names.

    `ROOT_DOCUMENT` fields -- the account and the order number -- live on the
    invoice header, not on the exploded line, and reading them off the line
    would return nothing for every order.
    """
    field = entity.fields.get(field_id)
    if field is None or field.physical_path is None:
        return _MISSING
    source = root if field.path_origin is PathOrigin.ROOT_DOCUMENT else record
    return _resolve(source, field.physical_path)


def _line_records(entity: EntityDefinition, document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The exploded line records, in document order.

    Only the declared `record_path` is walked, and only when the entity says it
    explodes. An entity that does not explode has one record and it is the
    document itself -- which is the correct reading even though `order_line`
    happens always to explode, because the reading comes from the declaration
    rather than from what today's declaration says.
    """
    if not entity.explode:
        return [document]
    current: Any = document
    for segment in entity.record_path:
        if not isinstance(current, Mapping) or segment not in current:
            return []
        current = current[segment]
    if not isinstance(current, list):
        return []
    return [element for element in current if isinstance(element, Mapping)]


def project_source_order_lines(
    schema: ActiveSchema, document: Mapping[str, Any]
) -> tuple[SourceOrderLine, ...]:
    """One sales document as the order's returnable lines.

    **A line with no identity is dropped, not numbered anyway.** The line
    reference is the schema's `line_number` where the source carries one, and
    the line's 1-based position in the declared record path where it does not --
    which is the only other identity the document offers, and is what the seeded
    sandbox orders have. A line that resolved to neither could not be selected,
    reserved or audited, so it is not offered.

    Comment lines are included if the source carries them: `line_type` is `CB`
    for a comment and `MP` for a product, and filtering on it here would be this
    module deciding what is returnable. That decision belongs to the policy, and
    a comment line simply has no ordered quantity to return.
    """
    entity = schema.entities.get(ORDER_LINE_ENTITY_ID)
    if entity is None:
        raise OrderLineSchemaUnavailableError(
            f"the active schema declares no {ORDER_LINE_ENTITY_ID!r} entity, so the "
            "order's lines cannot be projected"
        )
    lines: list[SourceOrderLine] = []
    for position, record in enumerate(_line_records(entity, document), start=1):
        reference = _text(
            _field_value(entity, _LINE_NUMBER_FIELD, root=document, record=record)
        ) or str(position)
        lines.append(
            SourceOrderLine(
                line_reference=reference,
                sku=_text(_field_value(entity, _SKU_FIELD, root=document, record=record)),
                description=_text(
                    _field_value(entity, _DESCRIPTION_FIELD, root=document, record=record)
                ),
                ordered_quantity=_whole(
                    _field_value(entity, _ORDERED_QUANTITY_FIELD, root=document, record=record)
                ),
                unit_price=_money(
                    _field_value(entity, _NET_PRICE_FIELD, root=document, record=record)
                ),
                product_reference=(
                    _text(_field_value(entity, _MASTER_PRODUCT_FIELD, root=document, record=record))
                    or _text(_field_value(entity, _PRODUCT_FIELD, root=document, record=record))
                ),
            )
        )
    return tuple(lines)


async def load_source_order_lines(
    collection: AsyncCollection[dict[str, object]],
    *,
    schema: ActiveSchema,
    order_reference: str,
) -> tuple[SourceOrderLine, ...] | None:
    """The order's lines, or `None` when the source holds no such order.

    `None` rather than an empty tuple, and the distinction is the same one the
    case projection makes everywhere: "there is no such order" and "the order has
    no lines" are different answers, and a route that collapsed them would render
    a mis-keyed case as an order somebody had emptied.

    The filter is built from the schema's own `sales_order_number` path, which is
    the field the sales collection is uniquely indexed on.
    """
    entity = schema.entities.get(ORDER_LINE_ENTITY_ID)
    if entity is None:
        raise OrderLineSchemaUnavailableError(
            f"the active schema declares no {ORDER_LINE_ENTITY_ID!r} entity, so the "
            "order's lines cannot be read"
        )
    field = entity.fields.get(SALES_ORDER_NUMBER_FIELD)
    if field is None or field.physical_path is None:
        raise OrderLineSchemaUnavailableError(
            f"{ORDER_LINE_ENTITY_ID}.{SALES_ORDER_NUMBER_FIELD} declares no physical path, "
            "so an order cannot be located in the source"
        )
    document = await collection.find_one({".".join(field.physical_path): order_reference})
    if document is None:
        return None
    return project_source_order_lines(schema, document)
