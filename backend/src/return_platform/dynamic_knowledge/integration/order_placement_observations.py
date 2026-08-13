"""Which warehouse the confirmed order shipped from (BAY-01).

A case has no `processingWarehouseReference`; a session does. That single
missing input is what kept the case flow from reaching the bay engine at all,
and inventing a warehouse for it would have been worse than the omission -- the
defect `bay_observations` exists to close is precisely a *missing* warehouse
reference being treated as "no constraint".

The order itself answers it honestly. `sales_order.ship_from_warehouse_id` is a
declared field of the active descriptor, sourced from `salesHdrEventData.
shipFromWhseId`, and the goods the associate is returning came from it.

Deliberately one read and one field. This resolves an *input* to placement; it
does not rank, score or decide anything, and `GraphWarehouseBayObservations`
remains the only thing that reads a warehouse's bays.

`None` covers both "the order declares no shipping warehouse" and "the graph
could not be read". They are logged apart, and they are not kept apart in the
return type on purpose: placement does the same thing for either, because
`observe_eligible_bays` already distinguishes the outcomes that oblige a caller
to behave differently. Manufacturing a third state here that nothing branches
on would be ceremony.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from return_platform.dynamic_knowledge.integration.targeted_sync import TargetedGraphAccess
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.schema import ActiveSchema

__all__ = [
    "ORDER_ENTITY_ID",
    "ORDER_NUMBER_FIELD_ID",
    "SHIP_FROM_WAREHOUSE_FIELD_ID",
    "GraphOrderPlacementObservations",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.order_placement_observations")

#: Schema vocabulary, named here rather than passed in for the reason
#: `bay_observations` names its own: a caller free to supply a different entity
#: id is free to point bay placement at something that is not an order.
ORDER_ENTITY_ID = "sales_order"
ORDER_NUMBER_FIELD_ID = "sales_order_number"
SHIP_FROM_WAREHOUSE_FIELD_ID = "ship_from_warehouse_id"


class GraphReadExecutor(Protocol):
    """`Neo4jKnowledgeGateway.execute`, and nothing else from it."""

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any: ...


class GraphOrderPlacementObservations:
    """Satisfies `operations.warehouse.case_placement.OrderPlacementObservationPort`."""

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        generation_handles: GenerationHandleProvider,
        knowledge_gateway: GraphReadExecutor,
        compiler: CypherCompiler | None = None,
    ) -> None:
        self._schema = schema
        self._generation_handles = generation_handles
        self._knowledge_gateway = knowledge_gateway
        self._compiler = compiler or CypherCompiler()

    @classmethod
    def from_access(cls, access: TargetedGraphAccess) -> GraphOrderPlacementObservations:
        return cls(
            schema=access.schema,
            generation_handles=access.generation_handles,
            knowledge_gateway=access.knowledge_gateway,
            compiler=access.compiler,
        )

    async def observe_shipping_warehouse(self, order_reference: str) -> str | None:
        """The order's shipping warehouse, read under a generation lease.

        No on-demand sync: the order was confirmed from this graph moments ago,
        so it is already present, and syncing it again on the bay path would
        pay for a read the confirmation already made.
        """
        if not order_reference:
            return None
        schema = self._schema
        if ORDER_ENTITY_ID not in schema.entities:
            logger.warning("order_placement_entity_absent", extra={"entity": ORDER_ENTITY_ID})
            return None
        plan = LogicalQueryPlan(
            operation=QueryOperation.FILTER,
            start_entity_id=ORDER_ENTITY_ID,
            fields=(ORDER_NUMBER_FIELD_ID, SHIP_FROM_WAREHOUSE_FIELD_ID),
            filters=(
                QueryCondition(
                    entity_id=ORDER_ENTITY_ID,
                    field_id=ORDER_NUMBER_FIELD_ID,
                    operator="EQUALS",
                    value=order_reference,
                ),
            ),
            # An order number is unique within an account and NOT globally --
            # the descriptor says so on the field itself. Two rows therefore
            # mean two different customers' orders, and picking one would put a
            # return into a stranger's warehouse. Read two so that is
            # detectable, and answer nothing when it happens.
            limit=2,
        )
        compiled = self._compiler.compile_read(schema, plan)
        async with self._generation_handles.acquire_read(schema) as handle:
            result = await self._knowledge_gateway.execute(
                schema=schema,
                graph_generation_id=handle.graph_generation_id,
                plan=plan,
                compiled_cypher=compiled.cypher,
                parameters=compiled.parameters,
            )
        rows = result.get("rows") if isinstance(result, dict) else None
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(
                "order_placement_warehouse_ambiguous",
                extra={"order_reference": order_reference, "row_count": len(rows)},
            )
            return None
        row = rows[0]
        if not isinstance(row, dict):  # pragma: no cover - gateway contract
            return None
        value = row.get(SHIP_FROM_WAREHOUSE_FIELD_ID)
        text = str(value).strip() if value is not None else ""
        return text or None
