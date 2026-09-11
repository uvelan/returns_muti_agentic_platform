"""Two identifying signals on two entities are one question, not two.

Observed in manual testing: "find orders for ACOSTA and the product is STRT
LGTH PEX-B POT". The graph held exactly one customer of that name with that
product on an order. The search returned five customers matched on the name
alone and five strangers' orders matched on the product alone, and the one
order that carried both was in neither list -- `customer_name` reads `customer`,
`product_description` reads `order_line`, and rows from two entities can never
merge into one candidate.

A `narrow_with` companion's `path` is the configuration that lets a narrowing
cross entities: the companion filter is applied where the companion lives, and
the search walks the declared path from there. These tests drive the shipped configuration through
the real catalogue, guard and compiler, and pin the validation that keeps a
mis-declared path from becoming a compiler error inside a turn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from return_platform.configuration.return_configuration import (
    DiscoveryConfiguration,
    IdentificationFieldConfiguration,
    load_return_configuration,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.guards import (
    GuardContext,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    SchemaQueryGuard,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan, QueryOperation
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    build_search_program,
    rank_search_results,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

REPOSITORY_BACKEND = Path(__file__).parents[2]
PRODUCTION_CONFIGURATION = REPOSITORY_BACKEND / "config/returns"
ACTIVE_SCHEMA = REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(ACTIVE_SCHEMA)


@pytest.fixture(scope="module")
def discovery() -> DiscoveryConfiguration:
    return load_return_configuration(PRODUCTION_CONFIGURATION).configuration.discovery


def _catalogue(discovery: DiscoveryConfiguration, schema: ActiveSchema) -> IdentificationCatalogue:
    return build_identification_catalogue(
        discovery.identification_fields,
        schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


@pytest.fixture(scope="module")
def catalogue(discovery: DiscoveryConfiguration, schema: ActiveSchema) -> IdentificationCatalogue:
    return _catalogue(discovery, schema)


def _intent(**signals: Any) -> OrderSearchIntent:
    return OrderSearchIntent.model_validate(signals)


def _compile(schema: ActiveSchema, plan: Any) -> str:
    context = GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )
    SchemaQueryGuard().validate(context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    return CypherCompiler().compile_read(schema, plan).cypher


# --- the shipped configuration ------------------------------------------------


def test_customer_and_product_together_are_two_intersecting_queries(
    catalogue: IdentificationCatalogue, schema: ActiveSchema
) -> None:
    program = build_search_program(
        _intent(customerNames=["ACOSTA"], productNames=["STRT LGTH PEX-B POT"]), catalogue
    )
    by_key = {planned.intent_key: planned.plan for planned in program.primary}
    assert set(by_key) == {"customerNames", "productNames"}

    customers = by_key["customerNames"]
    assert customers.start_entity_id == "order_line"
    assert [step.target_entity_id for step in customers.traversal] == ["sales_order", "customer"]
    assert {(f.entity_id, f.field_id) for f in customers.filters} == {
        ("customer", "customer_name"),
        ("order_line", "product_description"),
    }

    orders = by_key["productNames"]
    assert orders.start_entity_id == "customer"
    assert [step.target_entity_id for step in orders.traversal] == ["sales_order", "order_line"]
    assert {(f.entity_id, f.field_id) for f in orders.filters} == {
        ("customer", "customer_name"),
        ("order_line", "product_description"),
    }

    # Both reach the graph through the real guard and compiler, and both WHERE
    # clauses carry both signals: that is the intersection.
    for plan in (customers, orders):
        cypher = _compile(schema, plan)
        assert "PLACED_ORDER" in cypher and "HAS_ORDER_LINE" in cypher
        assert "customer_name" in cypher and "product_description" in cypher
        assert cypher.count(" CONTAINS ") == 2


def test_a_single_signal_still_runs_broad(catalogue: IdentificationCatalogue) -> None:
    """The narrowing is a companion, never a precondition."""
    customers = build_search_program(_intent(customerNames=["ACOSTA"]), catalogue)
    (plan,) = [planned.plan for planned in customers.primary]
    assert plan.start_entity_id == "customer" and plan.traversal == ()
    assert [f.field_id for f in plan.filters] == ["customer_name"]

    orders = build_search_program(_intent(productNames=["PEX-B POT"]), catalogue)
    (plan,) = [planned.plan for planned in orders.primary]
    assert plan.start_entity_id == "order_line" and plan.traversal == ()
    assert [f.field_id for f in plan.filters] == ["product_description"]
    assert plan.limit == 25


def test_narrowed_order_rows_rank_as_orders_matched_on_the_product(
    catalogue: IdentificationCatalogue,
) -> None:
    intent = _intent(customerNames=["ACOSTA"], productNames=["STRT LGTH PEX-B POT"])
    program = build_search_program(intent, catalogue)
    rows_by_key = {
        "customerNames": [
            {"account_id": "LENZ", "customer_id": "566970", "customer_name": "TIMOTHY ACOSTA"}
        ],
        "productNames": [
            {
                "sales_order_number": "CE579325",
                "account_id": "LENZ",
                "product_description": "1/2X20 STRT LGTH PEX-B POT WHIT",
                "ordered_quantity": 80,
            }
        ],
    }
    raw = [{"rows": rows_by_key[planned.intent_key], "count": 1} for planned in program.primary]
    ranked = rank_search_results(intent, raw, program=program)
    by_id = {candidate["candidate_id"]: candidate for candidate in ranked["candidates"]}
    assert set(by_id) == {"566970", "CE579325"}
    assert by_id["566970"]["matches"] == ["customer_name_contains"]
    assert by_id["CE579325"]["matches"] == ["product_description_contains"]
    assert by_id["CE579325"]["data"]["account_id"] == "LENZ"


# --- what a mis-declared path does ------------------------------------------


def _field(**overrides: Any) -> dict[str, Any]:
    base = yaml.safe_load(
        """
field_id: product_description
intent_key: productNames
label: product description
searches:
  - entity: order_line
    field: product_description
    strategy: CONTAINS
    narrow_with:
      - intent_key: customerNames
        path:
          - relationship: customer_placed_order
            direction: OUTBOUND
            target: sales_order
          - relationship: order_has_line
            direction: OUTBOUND
            target: order_line
"""
    )
    base["searches"][0].update(overrides)
    return base


def test_a_bare_intent_key_is_a_companion_on_the_searched_entity() -> None:
    field = IdentificationFieldConfiguration.model_validate(_field(narrow_with="quantities"))
    (narrowing,) = field.searches[0].narrow_with
    assert narrowing.intent_key == "quantities" and narrowing.path == ()


def test_a_path_that_does_not_arrive_at_the_searched_entity_is_refused_at_load() -> None:
    companion = _field()["searches"][0]["narrow_with"][0]
    companion["path"] = companion["path"][:1]
    with pytest.raises(ValueError, match="must arrive at the searched entity"):
        IdentificationFieldConfiguration.model_validate(_field(narrow_with=[companion]))


def test_a_companion_named_twice_is_refused_at_load() -> None:
    companion = _field()["searches"][0]["narrow_with"][0]
    with pytest.raises(ValueError, match="names a companion twice"):
        IdentificationFieldConfiguration.model_validate(
            _field(narrow_with=[companion, {"intent_key": "customerNames"}])
        )


def test_a_field_cannot_search_only_with_itself() -> None:
    payload = _field()
    payload["searches_only_with"] = "productNames"
    with pytest.raises(ValueError, match="cannot search only with itself"):
        IdentificationFieldConfiguration.model_validate(payload)


def test_a_return_entity_off_the_walk_is_refused() -> None:
    with pytest.raises(ValueError, match="neither the start entity nor one the traversal reaches"):
        LogicalQueryPlan(
            operation=QueryOperation.SEARCH, start_entity_id="customer", return_entity_id="product"
        )


def test_a_path_that_does_not_connect_makes_the_search_unusable_not_a_turn_error(
    discovery: DiscoveryConfiguration, schema: ActiveSchema
) -> None:
    """The schema is what says whether a hop connects, so that check waits for it."""
    payload = discovery.model_dump(mode="json")
    for entry in payload["identification_fields"]:
        if entry["field_id"] == "product_description":
            # Walking PLACED_ORDER inbound from customer arrives nowhere useful.
            entry["searches"][0]["narrow_with"][0]["path"][0]["direction"] = "INBOUND"
    broken = _catalogue(DiscoveryConfiguration.model_validate(payload), schema)
    field = broken.field_for("productNames")
    assert field is not None
    assert field.searches == ()
    assert any(
        "narrow_with" in problem.reason and "path" in problem.reason for problem in field.unusable
    )


# --- more than one companion, and a companion that searches only beside one -----


def test_three_signals_are_one_walk_that_returns_the_searched_entity(
    catalogue: IdentificationCatalogue, schema: ActiveSchema
) -> None:
    """Customer -> order -> line -> product, returning lines, all three filters.

    The colour sits on `product`, one hop *past* the searched line, and the
    customer three hops before it. The compiler chains hops, so the walk has to
    continue through the line to the product and name the line as what comes
    back.
    """
    program = build_search_program(
        _intent(customerNames=["ACOSTA"], productNames=["PEX-B POT"], colors=["white"]),
        catalogue,
    )
    orders = {planned.intent_key: planned.plan for planned in program.primary}["productNames"]
    assert orders.start_entity_id == "customer"
    assert [step.target_entity_id for step in orders.traversal] == [
        "sales_order",
        "order_line",
        "product",
    ]
    assert orders.return_entity_id == "order_line"
    assert {(f.entity_id, f.field_id) for f in orders.filters} == {
        ("order_line", "product_description"),
        ("customer", "customer_name"),
        ("product", "colour_finish"),
    }
    cypher = _compile(schema, orders)
    assert "REFERENCES_PRODUCT" in cypher and "colour_finish" in cypher
    assert "RETURN n2.`sales_order_number`" in cypher, "rows come from the line, not the product"
    assert program.needs_companion == ()


def test_a_colour_beside_a_product_narrows_it_from_the_product_side(
    catalogue: IdentificationCatalogue, schema: ActiveSchema
) -> None:
    program = build_search_program(
        _intent(productNames=["LAV FCT"], colors=["matte black"]), catalogue
    )
    (planned,) = program.primary
    assert planned.plan.start_entity_id == "product"
    assert [step.target_entity_id for step in planned.plan.traversal] == ["order_line"]
    assert planned.plan.return_entity_id is None
    assert "colour_finish" in _compile(schema, planned.plan)


def test_a_colour_alone_searches_nothing_and_says_what_it_needs(
    catalogue: IdentificationCatalogue,
) -> None:
    """The next question is the product, and this is where the model reads that."""
    intent = _intent(colors=["white"])
    program = build_search_program(intent, catalogue)
    assert program.primary == () and program.deferred == ()
    assert program.needs_companion == (("colors", "productNames"),)
    assert program.parsed.unusable_signals == ()

    ranked = rank_search_results(intent, [], program=program)
    assert ranked["candidates"] == []
    assert ranked["signals_needing_companion"] == [{"signal": "colors", "needs": "productNames"}]
    assert ranked["unsupported_signals"] == []


def test_a_colour_beside_a_customer_but_no_product_still_needs_the_product(
    catalogue: IdentificationCatalogue,
) -> None:
    program = build_search_program(_intent(customerNames=["ACOSTA"], colors=["white"]), catalogue)
    assert [planned.intent_key for planned in program.primary] == ["customerNames"]
    assert program.primary[0].plan.traversal == ()
    assert program.needs_companion == (("colors", "productNames"),)


def test_the_companion_rule_is_configuration(
    discovery: DiscoveryConfiguration, schema: ActiveSchema
) -> None:
    """Remove `searches_only_with` and a colour searches on its own again."""
    payload = discovery.model_dump(mode="json")
    for entry in payload["identification_fields"]:
        if entry["intent_key"] == "colors":
            entry["searches_only_with"] = None
    relaxed = _catalogue(DiscoveryConfiguration.model_validate(payload), schema)
    program = build_search_program(_intent(colors=["white"]), relaxed)
    (planned,) = program.primary
    assert planned.plan.start_entity_id == "product"
    assert program.needs_companion == ()
    described = {item["intentKey"]: item for item in relaxed.describe()}
    assert "searchesOnlyWith" not in described["colors"]

    shipped = {item["intentKey"]: item for item in _catalogue(discovery, schema).describe()}
    assert shipped["colors"]["searchesOnlyWith"] == "productNames"


def test_a_companion_whose_entity_the_walk_already_visits_adds_a_filter_and_no_hops(
    discovery: DiscoveryConfiguration, schema: ActiveSchema
) -> None:
    """An order number narrowing the same search sits on `sales_order`, which
    the customer path already walks through: one more predicate, no fork."""
    payload = discovery.model_dump(mode="json")
    for entry in payload["identification_fields"]:
        if entry["field_id"] == "product_description":
            entry["searches"][0]["narrow_with"].append(
                {
                    "intent_key": "orderNumbers",
                    "path": [
                        {
                            "relationship": "order_has_line",
                            "direction": "OUTBOUND",
                            "target": "order_line",
                        }
                    ],
                }
            )
    extended = _catalogue(DiscoveryConfiguration.model_validate(payload), schema)
    program = build_search_program(
        _intent(customerNames=["ACOSTA"], productNames=["PEX-B POT"], orderNumbers=["CE579325"]),
        extended,
    )
    orders = {planned.intent_key: planned.plan for planned in program.primary}["productNames"]
    assert [step.target_entity_id for step in orders.traversal] == ["sales_order", "order_line"]
    assert ("sales_order", "sales_order_number") in {
        (f.entity_id, f.field_id) for f in orders.filters
    }
    _compile(schema, orders)
