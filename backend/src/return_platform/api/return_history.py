"""What the graph knows about returns against an order, or against a customer.

This is the read `/api/cases` cannot serve, and the reason the return side was
projected into the graph at all. `/api/cases` answers "show me *this* case",
scoped to the caller's own cases and addressed by case id. The questions an
associate actually asks at the counter are the other shape:

* *Has this customer returned this before?* -- which needs every case reachable
  from the customer's orders, including cases another associate raised in
  another conversation.
* *What else is on this RMA?* -- which needs the items grouped under the return
  record that covers them, not under the case.
* *Where is this parcel now?* -- which needs the warehouse and bay a handling
  unit was staged into.

None of those is a lookup by a key the caller already holds, so none of them is
expressible against the operational collections without a join the client would
have to invent. They are traversals, and the graph is where traversals live.

**Not a second graph read path.** Every read below is a `LogicalQueryPlan` of
logical entity/field ids, validated by the same `SchemaQueryGuard` and
`QuerySafetyGuard` the agent's turns go through, compiled by the same
`CypherCompiler`, and executed by the same `Neo4jKnowledgeGateway`. No Cypher is
written here. Holding the console to the Order Discovery agent's own policy is
deliberate: the console must not be able to read an entity or a field the agent
is forbidden, and an allow-list with two sets of rules is an allow-list with a
hole in it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    validate_copilot_agent_binding,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.guards import (
    GuardContext,
    GuardRejected,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    SchemaQueryGuard,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
    TraversalStep,
)
from return_platform.dynamic_knowledge.lifecycle.handle import DEFAULT_SNAPSHOT_NAME
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
    MongoActiveRuntimeSnapshotStore,
)
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/return-history", tags=["Return History"])

# One page of history. Deliberately well under QuerySafetyPolicy.max_rows: a
# customer with two hundred returns is a report, not a counter conversation, and
# a screen that tries to render all of them helps nobody.
_MAX_ROWS = 50


class ReturnHistoryPlacement(BaseModel):
    """Where one parcel or pallet physically is."""

    model_config = ConfigDict(extra="forbid")

    handlingUnitId: str
    handlingUnitType: str | None = None
    physicalStatus: str | None = None
    warehouseId: str | None = None
    bayId: str | None = None
    trackingNumber: str | None = None


class ReturnHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returnItemId: str
    orderLineReference: str | None = None
    productReference: str | None = None
    quantity: int | None = None
    reason: str | None = None


class ReturnHistoryRecord(BaseModel):
    """One RMA and the items it covers -- decision D6, in the response shape.

    Items nest inside their record for the same reason they do on `/api/cases`:
    a flat pair of lists leaves the client to join them, and "label LBL-1 belongs
    to RMA-2" must not be something a consumer can get wrong.
    """

    model_config = ConfigDict(extra="forbid")

    returnRecordId: str
    returnReference: str | None = None
    status: str | None = None
    returnLocation: str | None = None
    trackingReference: str | None = None
    items: tuple[ReturnHistoryItem, ...] = ()


class ReturnHistoryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caseId: str
    status: str | None = None
    confirmedOrderReference: str | None = None
    createdAt: str | None = None
    returnRecords: tuple[ReturnHistoryRecord, ...] = ()
    # Lines named before any RMA covers them. Never folded into the first record:
    # the association is learned later and guessing it would be rendered as fact.
    unassignedItems: tuple[ReturnHistoryItem, ...] = ()
    placements: tuple[ReturnHistoryPlacement, ...] = ()


class ReturnHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Echoed back so a screen can say what it asked, not just what it got.
    orderReference: str | None = None
    accountId: str | None = None
    customerId: str | None = None
    cases: tuple[ReturnHistoryCase, ...] = ()


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _resources(request: Request) -> tuple[RuntimeResources, Settings]:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(resources, RuntimeResources) or not isinstance(settings, Settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_HISTORY_UNAVAILABLE",
                "message": "The platform runtime is not initialized.",
                "retryable": True,
            },
        )
    return resources, settings


async def _active_schema(resources: RuntimeResources, settings: Settings) -> ActiveSchema:
    """The schema the graph was built under, not the one on disk.

    `resolve_active_schema` prefers a published release and falls back to the
    file with a loud log, which is exactly what `GraphSyncService.refresh_schema`
    does before every sync. Reading under a different schema than the one that
    wrote would mean querying properties the nodes do not carry.
    """
    releases = (
        None
        if resources.mongo is None
        else SchemaReleaseStore(resources.mongo, settings.mongo_database)
    )
    return await resolve_active_schema(settings.dynamic_knowledge_schema_path, releases)


async def _active_generation(resources: RuntimeResources, settings: Settings) -> str:
    """Which generation serves this request.

    The same pointer `lifecycle/handle.py` resolves -- the `ActiveRuntimeSnapshot`
    the activation compare-and-swap moves -- so this surface and the order agent
    cannot disagree about what is live. No lease is taken: this is a single
    bounded read that completes inside one request, not a reasoning turn a
    retirement could outlive, and `acquire_read` yields unleased handles anyway
    when no lease store is configured.

    Falls back to `LEGACY_GENERATION_ID` when no rebuild has ever activated,
    which is what `MongoGraphStateProvider.active_generation` falls back to and
    what a graph predating the protocol is actually written under.
    """
    if resources.mongo is None:
        return LEGACY_GENERATION_ID
    snapshots = MongoActiveRuntimeSnapshotStore(
        resources.mongo[settings.mongo_database][ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION]
    )
    snapshot = await snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    return LEGACY_GENERATION_ID if snapshot is None else snapshot.graph_generation_id


def _copilot_agent_id(request: Request, schema: ActiveSchema) -> str:
    """Which policy bounds this surface -- resolved, never assumed.

    The console answers to the Order Discovery agent's own policy (see the module
    docstring), and *which* agent that is, is a deployment statement:
    `copilot.order_discovery_agent_id` in the active return configuration, the
    same value `/api/runtime-config` republishes to the Copilot. Read here rather
    than restated as a literal, because a literal is only correct until the next
    schema release renames the agent -- at which point the Copilot would follow
    the renamed mapping and this surface would keep naming a policy that no
    longer exists, failing with a `KeyError` and a 500. Same defect Phase 1
    removed from the frontend, one module further back.

    Not a fallback either. `validate_copilot_agent_binding` refuses an unset
    binding and one that names no published policy; both end as 503 with the
    guard's own sentence, because a console that quietly picks some other agent's
    permissions is worse than a console that says it is misconfigured.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_HISTORY_UNAVAILABLE",
                "message": "The return configuration is not loaded.",
                "retryable": True,
            },
        )
    try:
        return validate_copilot_agent_binding(loaded.configuration, schema.agent_policies.keys())
    except ValueError as invalid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "COPILOT_AGENT_CONFIGURATION_INVALID",
                "message": str(invalid),
                "retryable": False,
            },
        ) from invalid


def _guard_context(request: Request, schema: ActiveSchema, agent_id: str) -> GuardContext:
    principal = cast(Principal, request.state.principal)
    branch_ids_raw: Any = getattr(request.state, "branch_ids", ())
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies[agent_id],
        principal=PrincipalContext(
            principal_id=principal.subject,
            tenant_id=str(getattr(request.state, "tenant_id", "default")),
            roles=principal.roles,
            branch_ids=frozenset(str(value) for value in branch_ids_raw),
        ),
    )


class _GraphReader:
    """Plan -> guard -> compile -> execute, once, for every read on this surface."""

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
        schema: ActiveSchema,
        guard_context: GuardContext,
        graph_generation_id: str,
    ) -> None:
        self._gateway = Neo4jKnowledgeGateway(driver, database=database)
        self._schema = schema
        self._guard_context = guard_context
        # Which generation this surface answers from. It used to be
        # `LEGACY_GENERATION_ID` with a comment saying the value was
        # documentation because the compiler emitted no generation predicate and
        # the gateway discarded the argument. Both of those are now false: a
        # compiled read is pinned to this id, so passing a literal here would
        # answer every question from whatever `legacy-live` happens to hold.
        self._graph_generation_id = graph_generation_id
        self._schema_guard = SchemaQueryGuard()
        self._safety_guard = QuerySafetyGuard(QuerySafetyPolicy())
        self._compiler = CypherCompiler()

    async def rows(self, plan: LogicalQueryPlan) -> list[dict[str, Any]]:
        self._schema_guard.validate(self._guard_context, plan)
        self._safety_guard.validate(plan)
        compiled = self._compiler.compile_read(self._schema, plan)
        result = await self._gateway.execute(
            schema=self._schema,
            graph_generation_id=self._graph_generation_id,
            plan=plan,
            compiled_cypher=compiled.cypher,
            parameters=compiled.parameters,
        )
        rows = result.get("rows", []) if isinstance(result, dict) else []
        return [row for row in rows if isinstance(row, dict)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _item(row: dict[str, Any], return_item_id: str) -> ReturnHistoryItem:
    return ReturnHistoryItem(
        returnItemId=return_item_id,
        orderLineReference=_text(row.get("order_line_reference")),
        # Either shape's product column, because `operational_return_items`
        # carries both and neither writer fills the other's.
        productReference=_text(row.get("product_reference")) or _text(row.get("product_id")),
        quantity=_whole(row.get("quantity")) or _whole(row.get("requested_quantity")),
        reason=_text(row.get("return_reason")) or _text(row.get("reason_code")),
    )


def _items(rows: list[dict[str, Any]]) -> tuple[ReturnHistoryItem, ...]:
    """Rows with no identity are dropped rather than rendered as "None".

    A node written before a schema change can be missing a key property, and a
    row that cannot name itself is not something a screen can usefully show.
    """
    named = ((row, _text(row.get("return_item_id"))) for row in rows)
    return tuple(_item(row, item_id) for row, item_id in named if item_id is not None)


def _placement(row: dict[str, Any], handling_unit_id: str) -> ReturnHistoryPlacement:
    return ReturnHistoryPlacement(
        handlingUnitId=handling_unit_id,
        handlingUnitType=_text(row.get("handling_unit_type")),
        physicalStatus=_text(row.get("physical_status")),
        warehouseId=_text(row.get("warehouse_id")),
        bayId=_text(row.get("bay_id")),
        trackingNumber=_text(row.get("tracking_number")),
    )


def _placements(rows: list[dict[str, Any]]) -> tuple[ReturnHistoryPlacement, ...]:
    named = ((row, _text(row.get("handling_unit_id"))) for row in rows)
    return tuple(_placement(row, unit_id) for row, unit_id in named if unit_id is not None)


_CASE_FIELDS: tuple[str, ...] = (
    "case_id",
    "case_status",
    "confirmed_order_reference",
    "session_id",
    "created_at",
)
_RECORD_FIELDS: tuple[str, ...] = (
    "return_record_id",
    "case_id",
    "return_reference",
    "return_record_status",
    "return_location",
    "tracking_reference",
)
_ITEM_FIELDS: tuple[str, ...] = (
    "return_item_id",
    "case_id",
    "return_record_id",
    "order_line_reference",
    "product_reference",
    "product_id",
    "quantity",
    "requested_quantity",
    "return_reason",
    "reason_code",
)
_PLACEMENT_FIELDS: tuple[str, ...] = (
    "handling_unit_id",
    "session_id",
    "handling_unit_type",
    "physical_status",
    "warehouse_id",
    "bay_id",
    "tracking_number",
)

_TO_CASE: TraversalStep = TraversalStep(
    relationship_id="case_covers_order",
    direction="INBOUND",
    target_entity_id="return_case",
)
_TO_RECORD: TraversalStep = TraversalStep(
    relationship_id="case_raised_return_record",
    direction="OUTBOUND",
    target_entity_id="return_record",
)
# Through the case rather than through the record: an item exists before any RMA
# covers it, so traversing the RMA edge would silently drop exactly the items an
# associate is still waiting on. Each row carries its own `return_record_id`, so
# the grouping below loses nothing by taking the wider path.
_TO_ITEM: TraversalStep = TraversalStep(
    relationship_id="case_includes_return_item",
    direction="OUTBOUND",
    target_entity_id="return_item",
)
_TO_PLACEMENT: TraversalStep = TraversalStep(
    relationship_id="case_staged_as",
    direction="OUTBOUND",
    target_entity_id="return_handling_unit",
)


def _plan(
    *,
    start_entity_id: str,
    filters: tuple[QueryCondition, ...],
    traversal: tuple[TraversalStep, ...],
    fields: tuple[str, ...],
) -> LogicalQueryPlan:
    return LogicalQueryPlan(
        operation=QueryOperation.TRAVERSE,
        start_entity_id=start_entity_id,
        fields=fields,
        filters=filters,
        traversal=traversal,
        limit=_MAX_ROWS,
    )


@router.get("", response_model=APIResponse[ReturnHistory])
async def read_return_history(
    request: Request,
    orderReference: str | None = Query(  # noqa: N803 - the wire name, not a Python one
        default=None,
        max_length=200,
        description="Every return raised against this order number, by anyone.",
    ),
    accountId: str | None = Query(  # noqa: N803 - the wire name, not a Python one
        default=None,
        max_length=200,
        description="Branch account, with customerId: every return this customer has raised.",
    ),
    customerId: str | None = Query(  # noqa: N803 - the wire name, not a Python one
        default=None,
        max_length=200,
        description="ERP customer number, with accountId.",
    ),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnHistory]:
    """Returns against one order, or across one customer's orders.

    Exactly one of the two anchors, never both and never neither. A request with
    no anchor is "every return there has ever been", which is not a question this
    surface exists to answer; a request with both is two questions whose answers
    would be silently intersected.

    Not scoped to the caller's own cases, unlike `/api/cases`. That is the whole
    point: an associate asking whether this customer has returned this before is
    asking about returns somebody else raised. The anchor is a customer or an
    order the caller already has in front of them, so nothing is discoverable by
    guessing that the order search did not already surface.
    """
    order = _text(orderReference)
    account = _text(accountId)
    customer = _text(customerId)
    by_order = order is not None
    by_customer = account is not None and customer is not None
    if by_order == by_customer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "RETURN_HISTORY_ANCHOR_REQUIRED",
                "message": (
                    "Supply either orderReference, or both accountId and customerId -- not both "
                    "anchors and not neither."
                ),
                "retryable": False,
            },
        )

    resources, settings = _resources(request)
    if resources.neo4j is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_HISTORY_GRAPH_UNAVAILABLE",
                "message": "The knowledge graph is unavailable.",
                "retryable": True,
            },
        )
    schema = await _active_schema(resources, settings)
    reader = _GraphReader(
        driver=resources.neo4j,
        database=settings.neo4j_database,
        schema=schema,
        guard_context=_guard_context(request, schema, _copilot_agent_id(request, schema)),
        graph_generation_id=await _active_generation(resources, settings),
    )

    start_entity_id: str
    filters: tuple[QueryCondition, ...]
    prefix: tuple[TraversalStep, ...]
    if by_order:
        start_entity_id = "sales_order"
        filters = (
            QueryCondition(
                entity_id="sales_order",
                field_id="sales_order_number",
                operator="EQUALS",
                value=order,
            ),
        )
        prefix = ()
    else:
        start_entity_id = "customer"
        filters = (
            QueryCondition(
                entity_id="customer", field_id="account_id", operator="EQUALS", value=account
            ),
            QueryCondition(
                entity_id="customer", field_id="customer_id", operator="EQUALS", value=customer
            ),
        )
        prefix = (
            TraversalStep(
                relationship_id="customer_placed_order",
                direction="OUTBOUND",
                target_entity_id="sales_order",
            ),
        )

    def plan(steps: tuple[TraversalStep, ...], fields: tuple[str, ...]) -> LogicalQueryPlan:
        return _plan(
            start_entity_id=start_entity_id,
            filters=filters,
            traversal=prefix + steps,
            fields=fields,
        )

    try:
        case_rows = await reader.rows(plan((_TO_CASE,), _CASE_FIELDS))
        record_rows = await reader.rows(plan((_TO_CASE, _TO_RECORD), _RECORD_FIELDS))
        item_rows = await reader.rows(plan((_TO_CASE, _TO_ITEM), _ITEM_FIELDS))
        placement_rows = await reader.rows(plan((_TO_CASE, _TO_PLACEMENT), _PLACEMENT_FIELDS))
    except GuardRejected as rejected:
        # A guard refusal here is a configuration fault, not a client mistake:
        # this surface builds its own plans, so the only way one is refused is
        # that the active schema no longer permits an entity, field or operator
        # it names. Reported as 422 with the guard's own code so the cause is
        # readable rather than hidden behind a generic 500.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": rejected.code,
                "message": rejected.message,
                "retryable": False,
            },
        ) from rejected

    items_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in item_rows:
        items_by_case[str(row.get("case_id", ""))].append(row)
    records_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in record_rows:
        records_by_case[str(row.get("case_id", ""))].append(row)
    # Attributed by session rather than by case: a handling unit carries the
    # session it belongs to and never the case, so the case's own session_id is
    # the only join there is.
    placements_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in placement_rows:
        session = _text(row.get("session_id"))
        if session is not None:
            placements_by_session[session].append(row)

    cases: list[ReturnHistoryCase] = []
    seen: set[str] = set()
    for row in case_rows:
        case_id = _text(row.get("case_id"))
        if case_id is None or case_id in seen:
            # One case can match the same order number in two accounts, so the
            # same case node comes back twice. Deduplicated here rather than in
            # the query: DISTINCT would apply to the whole row, and the rows
            # differ only by the order they were reached through.
            continue
        seen.add(case_id)
        case_items = items_by_case.get(case_id, [])
        by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unassigned: list[dict[str, Any]] = []
        for item in case_items:
            record_id = _text(item.get("return_record_id"))
            if record_id is None:
                unassigned.append(item)
            else:
                by_record[record_id].append(item)
        records: list[ReturnHistoryRecord] = []
        for record in records_by_case.get(case_id, []):
            record_id = _text(record.get("return_record_id"))
            if record_id is None:
                continue
            records.append(
                ReturnHistoryRecord(
                    returnRecordId=record_id,
                    returnReference=_text(record.get("return_reference")),
                    status=_text(record.get("return_record_status")),
                    returnLocation=_text(record.get("return_location")),
                    trackingReference=_text(record.get("tracking_reference")),
                    items=_items(by_record.get(record_id, [])),
                )
            )
        session = _text(row.get("session_id"))
        cases.append(
            ReturnHistoryCase(
                caseId=case_id,
                status=_text(row.get("case_status")),
                confirmedOrderReference=_text(row.get("confirmed_order_reference")),
                createdAt=_text(row.get("created_at")),
                returnRecords=tuple(records),
                unassignedItems=_items(unassigned),
                placements=_placements(
                    [] if session is None else placements_by_session.get(session, [])
                ),
            )
        )

    return APIResponse(
        data=ReturnHistory(
            orderReference=order,
            accountId=account if by_customer else None,
            customerId=customer if by_customer else None,
            cases=tuple(cases),
        ),
        meta=_meta(request),
    )
