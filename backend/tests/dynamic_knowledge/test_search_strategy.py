from __future__ import annotations

from pathlib import Path

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.guards import (
    AnchorValue,
    GuardContext,
    GuardRejected,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    SchemaQueryGuard,
    StrongAnchorGuard,
    StrongAnchorRequest,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    MAX_CACHED_CANDIDATES,
    build_customer_fuzzy_probe_plan,
    build_progressive_plans,
    fuzzy_match_customers,
    rank_search_results,
    search_intent_signature,
    unsupported_signals,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    root = Path(__file__).parents[2]
    return load_active_schema(root / "config/dynamic_knowledge/active-schema.return-order.yaml")


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )


# --- build_progressive_plans -------------------------------------------------


def test_single_order_number_produces_one_exact_plan() -> None:
    intent = OrderSearchIntent(orderNumbers=("10001",))
    plans = build_progressive_plans(intent)
    assert len(plans) == 1
    assert plans[0].start_entity_id == "sales_order"
    assert plans[0].filters[0].operator == "EXACT"


def test_partial_info_with_no_order_number_still_produces_plans() -> None:
    """A customer with no order number but a name and a rough date range."""
    intent = OrderSearchIntent(
        customerNames=("Smith",),
        dateFrom="2026-07-20",
        dateTo="2026-07-28",
    )
    plans = build_progressive_plans(intent)
    assert len(plans) == 2
    entities = {plan.start_entity_id for plan in plans}
    assert entities == {"customer", "sales_order"}
    date_plan = next(p for p in plans if p.start_entity_id == "sales_order")
    assert date_plan.filters[0].operator == "BETWEEN"
    assert date_plan.filters[0].value == {"from": "2026-07-20", "to": "2026-07-28"}


def test_combination_of_product_and_quantity_builds_joint_filter() -> None:
    intent = OrderSearchIntent(productNames=("faucet",), quantities=(2,))
    plans = build_progressive_plans(intent)
    # One plan for the product-name pass, one combined product+quantity pass.
    assert len(plans) == 2
    combined = next(p for p in plans if len(p.filters) == 2)
    fields = {f.field_id for f in combined.filters}
    assert fields == {"ordered_quantity", "product_description"}


def test_approximate_date_becomes_same_day_between() -> None:
    intent = OrderSearchIntent(approximateDate="2026-07-25")
    plans = build_progressive_plans(intent)
    assert len(plans) == 1
    assert plans[0].filters[0].operator == "BETWEEN"
    assert plans[0].filters[0].value == {"from": "2026-07-25", "to": "2026-07-25"}


def test_unsupported_signals_do_not_silently_disappear(caplog: pytest.LogCaptureFixture) -> None:
    """Address/colour have no backing schema field yet - they must be surfaced,
    not just dropped, so a missing result is diagnosable."""
    intent = OrderSearchIntent(streetAddresses=("18 Main Street",), colors=("blue",))
    with caplog.at_level("WARNING"):
        plans = build_progressive_plans(intent)
    assert plans == []
    assert any("unsupported" in record.message for record in caplog.records)


def test_empty_intent_produces_no_plans() -> None:
    assert build_progressive_plans(OrderSearchIntent()) == []


# --- rank_search_results ------------------------------------------------------


def test_rank_reads_rows_key_matching_real_execute_shape() -> None:
    """Neo4jKnowledgeGateway.execute() returns {"rows": [...], "count": N} -
    ranking must read that key, not a "nodes" key that execute() never sends."""
    intent = OrderSearchIntent(orderNumbers=("10001",))
    raw_results = [{"rows": [{"sales_order_number": "10001", "customer_id": "C1"}], "count": 1}]
    ranked = rank_search_results(intent, raw_results)
    assert ranked["total_found"] == 1
    assert ranked["candidates"][0]["data"]["sales_order_number"] == "10001"
    assert "order_number_exact" in ranked["candidates"][0]["matches"]


def test_rank_merges_rows_for_the_same_order_across_plans() -> None:
    intent = OrderSearchIntent(customerNames=("Smith",), productNames=("faucet",))
    raw_results = [
        {"rows": [{"sales_order_number": "10001", "customer_name": "John Smith"}]},
        {"rows": [{"sales_order_number": "10001", "product_description": "Moen faucet"}]},
    ]
    ranked = rank_search_results(intent, raw_results)
    assert ranked["total_found"] == 1
    merged = ranked["candidates"][0]["data"]
    assert merged["customer_name"] == "John Smith"
    assert merged["product_description"] == "Moen faucet"


def test_rank_reports_unsupported_signals_alongside_results() -> None:
    intent = OrderSearchIntent(orderNumbers=("10001",), colors=("blue",))
    ranked = rank_search_results(intent, [])
    assert ranked["unsupported_signals"] == ["colors"]


def test_rank_caps_candidates_at_max_cached_but_reports_true_total() -> None:
    """Pagination depends on total_found staying accurate even once the merged
    candidate list itself is capped for caching/token-budget reasons."""
    intent = OrderSearchIntent(customerNames=("Maya",))
    rows = [{"customer_id": f"CUST-{i}", "customer_name": f"Maya {i}"} for i in range(40)]
    ranked = rank_search_results(intent, [{"rows": rows}])
    assert len(ranked["candidates"]) == MAX_CACHED_CANDIDATES
    assert ranked["total_found"] == 40


# --- search_intent_signature --------------------------------------------------


def test_signature_ignores_metadata_but_not_identifying_fields() -> None:
    base = OrderSearchIntent(customerNames=("Maya",))
    same_search_next_page = OrderSearchIntent(
        customerNames=("Maya",), wantsMoreResults=True, confidence=0.9
    )
    different_search = OrderSearchIntent(customerNames=("John",))

    assert search_intent_signature(base) == search_intent_signature(same_search_next_page)
    assert search_intent_signature(base) != search_intent_signature(different_search)


# --- fuzzy_match_customers ----------------------------------------------------


def test_fuzzy_match_recovers_an_obvious_misspelling() -> None:
    rows = [
        {"customer_id": "C1", "customer_name": "John Smith"},
        {"customer_id": "C2", "customer_name": "Priya Nair"},
    ]
    matches = fuzzy_match_customers(("Jhon Smith",), rows)
    assert [row["customer_id"] for row in matches] == ["C1"]


def test_fuzzy_match_rejects_unrelated_names() -> None:
    rows = [{"customer_id": "C1", "customer_name": "Priya Nair"}]
    assert fuzzy_match_customers(("Zephyrine Okonkwo",), rows) == []


def test_fuzzy_match_ranks_closest_first_and_respects_limit() -> None:
    rows = [
        {"customer_id": "C1", "customer_name": "Jon Smith"},
        {"customer_id": "C2", "customer_name": "John Smith"},
        {"customer_id": "C3", "customer_name": "Jonathan Smithson"},
    ]
    matches = fuzzy_match_customers(("John Smith",), rows, limit=2)
    assert len(matches) == 2
    assert matches[0]["customer_id"] == "C2"  # exact spelling ranks above near-misses


# --- build_customer_fuzzy_probe_plan ------------------------------------------


def test_fuzzy_probe_plan_passes_schema_guard_and_compiles(
    production_schema: ActiveSchema,
) -> None:
    """The misspelling fallback issues one unfiltered, bounded read of customers —
    it must clear the same guard rails as every other plan, not bypass them just
    because it has no WHERE clause."""
    plan = build_customer_fuzzy_probe_plan()
    guard_context = _guard_context(production_schema)
    SchemaQueryGuard().validate(guard_context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    compiled = CypherCompiler().compile_read(production_schema, plan)
    assert compiled.read_only is True
    assert "WHERE" not in compiled.cypher


# --- full guard + compiler round-trip against the production schema ---------


def test_every_progressive_plan_passes_schema_guard_and_compiles(
    production_schema: ActiveSchema,
) -> None:
    """Every field/operator combination search_strategy.py builds must actually
    be legal against the real schema — this is what closes the gap where
    ORDER_SEARCH used to bypass SchemaQueryGuard entirely."""
    intent = OrderSearchIntent(
        orderNumbers=("10001",),
        customerNames=("Smith",),
        skus=("12345",),
        productNames=("faucet",),
        quantities=(2,),
        dateFrom="2026-07-20",
        dateTo="2026-07-28",
        freeTextTerms=("ORD-10001",),
    )
    plans = build_progressive_plans(intent)
    assert len(plans) >= 6

    guard_context = _guard_context(production_schema)
    compiler = CypherCompiler()
    query_safety = QuerySafetyGuard(QuerySafetyPolicy())
    for plan in plans:
        SchemaQueryGuard().validate(guard_context, plan)
        query_safety.validate(plan)
        compiled = compiler.compile_read(production_schema, plan)
        assert compiled.read_only is True


def test_approximate_date_plan_passes_guard_with_between_not_exact(
    production_schema: ActiveSchema,
) -> None:
    """delivered_at only supports range operators in the real schema - a
    same-day match must use BETWEEN, never EXACT/EQUALS."""
    plans = build_progressive_plans(OrderSearchIntent(approximateDate="2026-07-25"))
    guard_context = _guard_context(production_schema)
    SchemaQueryGuard().validate(guard_context, plans[0])
    CypherCompiler().compile_read(production_schema, plans[0])


# --- new strong anchors on the production schema -----------------------------


def test_combination_anchor_requires_both_fields(production_schema: ActiveSchema) -> None:
    guard_context = _guard_context(production_schema)
    with pytest.raises(GuardRejected) as error:
        StrongAnchorGuard().validate(
            guard_context,
            StrongAnchorRequest(
                entity_id="sales_order",
                strong_anchor_id="probable_order_by_customer_and_status",
                anchors=(
                    AnchorValue(
                        field_id="customer_id",
                        operator="EQUALS",
                        value="C1",
                        value_origin="USER_MESSAGE",
                    ),
                ),
            ),
        )
    assert error.value.code == "ON_DEMAND_SYNC_STRONG_ANCHOR_REQUIRED"


def test_combination_anchor_accepts_customer_and_status_together(
    production_schema: ActiveSchema,
) -> None:
    guard_context = _guard_context(production_schema)
    normalized = StrongAnchorGuard().validate(
        guard_context,
        StrongAnchorRequest(
            entity_id="sales_order",
            strong_anchor_id="probable_order_by_customer_and_status",
            anchors=(
                AnchorValue(
                    field_id="customer_id",
                    operator="EQUALS",
                    value="C1",
                    value_origin="USER_MESSAGE",
                ),
                AnchorValue(
                    field_id="order_status",
                    operator="EQUALS",
                    value="SHIPPED",
                    value_origin="USER_MESSAGE",
                ),
            ),
        ),
    )
    assert normalized == {"customer_id": "C1", "order_status": "SHIPPED"}


def test_order_line_now_has_a_strong_anchor(production_schema: ActiveSchema) -> None:
    # The anchor is the line's real composite key. It used to be a single
    # synthetic `order_line_key`, which the salesInv documents do not carry --
    # a line is identified by its branch, its order, and its line number.
    guard_context = _guard_context(production_schema)
    normalized = StrongAnchorGuard().validate(
        guard_context,
        StrongAnchorRequest(
            entity_id="order_line",
            strong_anchor_id="exact_order_line",
            anchors=(
                AnchorValue(
                    field_id="account_id",
                    operator="EXACT",
                    value="OHVAL",
                    value_origin="USER_MESSAGE",
                ),
                AnchorValue(
                    field_id="sales_order_number",
                    operator="EXACT",
                    value="CW273354",
                    value_origin="USER_MESSAGE",
                ),
                AnchorValue(
                    field_id="line_number",
                    operator="EXACT",
                    value="1",
                    value_origin="USER_MESSAGE",
                ),
            ),
        ),
    )
    assert normalized == {
        "account_id": "OHVAL",
        "sales_order_number": "CW273354",
        "line_number": "1",
    }


def test_no_strong_anchor_is_inert(production_schema: ActiveSchema) -> None:
    """Every field a strong anchor names must actually permit on-demand sync.

    An anchor whose fields are not `on_demand_sync_anchor` is declared but
    dead: `StrongAnchorGuard` rejects every request against it, so the
    escalation path it exists to enable can never run. Nothing else notices --
    the schema loads, the anchor appears in the config, and the only symptom is
    a sync that silently never happens.

    Three of the nine anchors were in that state, including both anchors keyed
    on `account_id`, so this asserts the invariant across the whole schema
    rather than re-testing each anchor by hand.
    """
    inert = [
        f"{entity_id}.{anchor_id} -> {anchor_field.field_id}"
        for entity_id, entity in production_schema.entities.items()
        for anchor_id, anchor in entity.strong_anchors.items()
        for anchor_field in anchor.fields
        if not entity.fields[anchor_field.field_id].capabilities.on_demand_sync_anchor
        or not entity.fields[anchor_field.field_id].permissions.on_demand_sync_by
    ]
    assert inert == []


def test_fuzzy_match_survives_a_trade_name_suffix() -> None:
    """The real failure: a misspelt name plus a suffix nobody types.

    "MELGON HEATING & COOLING" scores 0.667 against "melgan heatng" on a plain
    whole-string ratio -- under the threshold -- while the next best customer in
    the same probe scores 0.370. The name matched and the untyped tail diluted
    it, so the one case the fallback exists for was the case it missed.
    """
    rows = [
        {"customer_id": "471565", "customer_name": "MELGON HEATING & COOLING"},
        {"customer_id": "1379433", "customer_name": "ALLIED GAS"},
        {"customer_id": "1013086", "customer_name": "THE TANKLESS GUYS"},
    ]
    matches = fuzzy_match_customers(("melgan heatng",), rows)
    assert [row["customer_id"] for row in matches] == ["471565"]


# ---------------------------------------------------------------------------
# Identification signals the agent asks for and can now use
#
# The clarification policy ranks email at 95 and phone at 90 -- above every
# narrowing signal -- and `OrderSearchIntent` had no field for either. The agent
# asked an associate for the email on the order and then had nowhere to put the
# answer. Address, city, state and postal code were declared but dropped, long
# after the schema grew real properties for all of them.
# ---------------------------------------------------------------------------


def _fields_asked(plans: list[LogicalQueryPlan]) -> set[tuple[str, str, str]]:
    return {
        (condition.entity_id, condition.field_id, condition.operator)
        for plan in plans
        for condition in plan.filters
    }


def test_an_email_the_associate_supplies_is_searched() -> None:
    """The answer to the highest-priority clarifying question after the anchors."""
    plans = build_progressive_plans(OrderSearchIntent(emails=("dana@example.com",)))

    assert ("contact_point", "email", "EXACT") in _fields_asked(plans)


def test_a_partial_email_is_matched_loosely() -> None:
    """A fragment is often all an associate can read off a screen.

    EXACT on it would find nothing, and finding nothing looks identical to the
    customer not existing.
    """
    plans = build_progressive_plans(OrderSearchIntent(emails=("dana at example",)))

    assert ("contact_point", "email", "CONTAINS") in _fields_asked(plans)


def test_a_phone_is_tried_both_as_typed_and_as_digits() -> None:
    """Spoken and stored forms rarely agree, and neither can be assumed.

    "(214) 555-0142" against a stored "2145550142" matches on neither side
    unless both are asked for.
    """
    plans = build_progressive_plans(OrderSearchIntent(phones=("(214) 555-0142",)))
    asked = {(condition.field_id, condition.value) for plan in plans for condition in plan.filters}

    assert ("phone_number", "(214) 555-0142") in asked
    assert ("phone_number", "2145550142") in asked
    # The order side records its own ship-to phone, and it is EXACT-only, so
    # only the digits form can match there.
    assert ("ship_to_phone", "2145550142") in asked


def test_two_spellings_of_one_number_are_not_searched_twice() -> None:
    """`dict.fromkeys` dedupes what was typed, not what is asked."""
    plans = build_progressive_plans(OrderSearchIntent(phones=("(214) 555-0142", "2145550142")))
    order_side = [
        condition
        for plan in plans
        for condition in plan.filters
        if condition.field_id == "ship_to_phone"
    ]

    assert len(order_side) == 1


def test_a_city_is_searched_where_the_customer_is_and_where_the_order_went() -> None:
    """Two different questions, and an associate saying "Dallas" could mean either.

    Asking only the contact point would miss an order shipped to a job site;
    asking only the order would miss a customer whose orders went elsewhere.
    """
    asked = _fields_asked(build_progressive_plans(OrderSearchIntent(cities=("Dallas",))))

    assert ("contact_point", "city", "CONTAINS") in asked
    assert ("sales_order", "ship_to_city", "CONTAINS") in asked


def test_state_and_postal_code_use_the_operator_the_schema_allows() -> None:
    """CONTAINS on either is refused by the schema guard, losing the whole pass.

    The failure is silent from the associate's side: they gave a ZIP and got
    nothing back.
    """
    asked = _fields_asked(
        build_progressive_plans(OrderSearchIntent(states=("TX",), postalCodes=("75201",)))
    )

    assert ("contact_point", "state", "EXACT") in asked
    assert ("sales_order", "ship_to_state", "EXACT") in asked
    assert ("contact_point", "postal_code", "EXACT") in asked
    assert ("sales_order", "ship_to_postal_code", "EXACT") in asked


def test_a_street_is_searched_only_where_a_street_exists() -> None:
    """`sales_order` records the ship-to city, state and ZIP but not the street.

    Naming a field that does not exist would fail the schema guard and take the
    address pass with it, so the order side is skipped rather than guessed at.
    """
    plans = build_progressive_plans(OrderSearchIntent(streetAddresses=("120 Beacon St",)))
    entities = {plan.start_entity_id for plan in plans}

    assert entities == {"contact_point"}


def test_colour_is_still_reported_rather_than_guessed() -> None:
    """No entity carries a colour property, and pretending otherwise is worse.

    Matching "blue" against product_description would put "Blue Ridge Faucet"
    in front of someone who said the tap was blue, with no sign the colour had
    been matched loosely.
    """
    intent = OrderSearchIntent(colors=("blue",))

    assert build_progressive_plans(intent) == []
    assert unsupported_signals(intent) == ("colors",)


def test_the_search_signature_moves_when_a_contact_detail_changes() -> None:
    """Otherwise a "show next" pages through someone else's cached results."""
    first = OrderSearchIntent(emails=("dana@example.com",))
    second = OrderSearchIntent(emails=("sam@example.com",))

    assert search_intent_signature(first) != search_intent_signature(second)
    assert search_intent_signature(
        OrderSearchIntent(phones=("2145550142",))
    ) != search_intent_signature(OrderSearchIntent(phones=("2145550143",)))
