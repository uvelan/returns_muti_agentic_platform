"""The console's return-history read, held to the agent's own policy.

The endpoint builds its own `LogicalQueryPlan`s rather than accepting one, so
the risk is not a malicious caller -- it is that a plan this module hard-codes
stops being one the active schema permits. That failure surfaces at request time
as a guard rejection nobody expected, so it is pinned here against the real
descriptor, the real guards and the real Cypher compiler.

No HTTP here. What is under test is the eight plans this surface issues; standing
up the app would add a lifespan, a Mongo client and a Neo4j driver without
exercising one more line of the thing being asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from return_platform.api.return_history import (
    _CASE_FIELDS,
    _ITEM_FIELDS,
    _PLACEMENT_FIELDS,
    _RECORD_FIELDS,
    _TO_CASE,
    _TO_ITEM,
    _TO_PLACEMENT,
    _TO_RECORD,
    _plan,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
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
from return_platform.dynamic_knowledge.schema import ActiveSchema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
AGENT_ID = "order-discovery-agent"

ORDER_FILTERS = (
    QueryCondition(
        entity_id="sales_order",
        field_id="sales_order_number",
        operator="EQUALS",
        value="CW273354",
    ),
)
CUSTOMER_FILTERS = (
    QueryCondition(
        entity_id="customer", field_id="account_id", operator="EQUALS", value="CHARLOTTE"
    ),
    QueryCondition(entity_id="customer", field_id="customer_id", operator="EQUALS", value="9911"),
)
CUSTOMER_PREFIX = (
    TraversalStep(
        relationship_id="customer_placed_order",
        direction="OUTBOUND",
        target_entity_id="sales_order",
    ),
)
READS = (
    ("case", (_TO_CASE,), _CASE_FIELDS),
    ("record", (_TO_CASE, _TO_RECORD), _RECORD_FIELDS),
    ("item", (_TO_CASE, _TO_ITEM), _ITEM_FIELDS),
    ("placement", (_TO_CASE, _TO_PLACEMENT), _PLACEMENT_FIELDS),
)


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


@pytest.fixture(scope="module")
def guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id="associate-1",
            tenant_id="tenant-a",
            roles=frozenset({"associate"}),
            branch_ids=frozenset({"CHARLOTTE"}),
        ),
    )


def _order_plan(steps: tuple[TraversalStep, ...], fields: tuple[str, ...]) -> LogicalQueryPlan:
    return _plan(
        start_entity_id="sales_order", filters=ORDER_FILTERS, traversal=steps, fields=fields
    )


def _customer_plan(steps: tuple[TraversalStep, ...], fields: tuple[str, ...]) -> LogicalQueryPlan:
    return _plan(
        start_entity_id="customer",
        filters=CUSTOMER_FILTERS,
        traversal=CUSTOMER_PREFIX + steps,
        fields=fields,
    )


@pytest.mark.parametrize(("name", "steps", "fields"), READS)
def test_every_order_anchored_read_passes_the_real_guards_and_compiles(
    schema: ActiveSchema,
    guard_context: GuardContext,
    name: str,
    steps: tuple[TraversalStep, ...],
    fields: tuple[str, ...],
) -> None:
    plan = _order_plan(steps, fields)

    SchemaQueryGuard().validate(guard_context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    compiled = CypherCompiler().compile_read(schema, plan)

    assert compiled.cypher.startswith("MATCH"), name
    # `Neo4jKnowledgeGateway.execute` refuses anything that writes, so a read
    # that grew a mutating clause would be rejected at the driver rather than
    # here -- catching it at compile time names the plan that did it.
    assert "MERGE" not in compiled.cypher


@pytest.mark.parametrize(("name", "steps", "fields"), READS)
def test_every_customer_anchored_read_passes_the_real_guards_and_compiles(
    schema: ActiveSchema,
    guard_context: GuardContext,
    name: str,
    steps: tuple[TraversalStep, ...],
    fields: tuple[str, ...],
) -> None:
    """One hop deeper than the order-anchored form, and the deepest this surface
    goes. `QuerySafetyPolicy.max_traversal_depth` is 4; a fifth hop added here
    would be refused at request time with a budget error."""
    plan = _customer_plan(steps, fields)

    SchemaQueryGuard().validate(guard_context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)

    assert len(plan.traversal) <= QuerySafetyPolicy().max_traversal_depth
    assert CypherCompiler().compile_read(schema, plan).cypher.startswith("MATCH")


def test_the_customer_read_goes_through_the_order_to_reach_the_case(
    schema: ActiveSchema,
) -> None:
    """There is no customer-to-case edge, and inventing one would be a claim the
    data does not support: a case records the order it was raised against, not
    who placed it."""
    compiled = CypherCompiler().compile_read(schema, _customer_plan((_TO_CASE,), _CASE_FIELDS))

    assert "MATCH (n0)-[r1:`PLACED_ORDER`]->(n1:`SalesOrder`)" in compiled.cypher
    assert "MATCH (n1)<-[r2:`COVERS_ORDER`]-(n2:`ReturnCase`)" in compiled.cypher


def test_the_item_read_reaches_items_through_the_case_not_the_rma(
    schema: ActiveSchema,
) -> None:
    """An item exists on the case before Support says which RMA covers it.

    Traversing the RMA edge instead would silently drop exactly the lines an
    associate is waiting on -- and each row carries its own `return_record_id`,
    so the wider path loses nothing.
    """
    compiled = CypherCompiler().compile_read(
        schema, _order_plan((_TO_CASE, _TO_ITEM), _ITEM_FIELDS)
    )

    assert "INCLUDES_RETURN_ITEM" in compiled.cypher
    assert "COVERS_RETURN_ITEM" not in compiled.cypher
    assert "return_record_id" in compiled.cypher


def test_the_case_read_selects_the_session_that_attributes_a_placement(
    schema: ActiveSchema,
) -> None:
    """A handling unit records its session and never its case, so the case's own
    `session_id` is the only thing that can attribute a parcel to a return.
    Dropping it from the selected fields would leave every placement orphaned in
    the response."""
    assert "session_id" in _CASE_FIELDS
    assert "session_id" in _PLACEMENT_FIELDS

    compiled = CypherCompiler().compile_read(schema, _order_plan((_TO_CASE,), _CASE_FIELDS))

    assert "AS `session_id`" in compiled.cypher


def test_every_selected_field_is_one_the_policy_allows_this_role_to_see(
    schema: ActiveSchema, guard_context: GuardContext
) -> None:
    """The reason this surface reuses the agent's guard rather than reading the
    graph directly: the console must not become a way to see a field the agent
    is forbidden."""
    for _name, steps, fields in READS:
        target = steps[-1].target_entity_id
        for field_id in fields:
            permitted = schema.entities[target].fields[field_id].permissions.displayable_by
            assert "*" in permitted or permitted & guard_context.principal.roles, field_id


def test_a_read_outside_the_policy_is_refused_rather_than_compiled(
    schema: ActiveSchema, guard_context: GuardContext
) -> None:
    """The guard, not this module's own field lists, is what bounds the surface.

    Asserted against an entity that is projected and deliberately *not*
    allow-listed, so this fails if the allow-list ever becomes decorative.
    """
    policy = schema.agent_policies[AGENT_ID]
    forbidden = next(
        entity_id
        for entity_id in sorted(schema.entities)
        if entity_id not in policy.allowed_entity_ids
        and any(node.entity_id == entity_id for node in schema.graph.nodes.values())
    )
    plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id=forbidden,
        fields=(),
        filters=(),
        traversal=(),
        limit=1,
    )

    with pytest.raises(GuardRejected) as rejected:
        SchemaQueryGuard().validate(guard_context, plan)

    assert rejected.value.code == "ORDER_AGENT_OUT_OF_SCOPE"
