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
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    MAX_CACHED_CANDIDATES,
    build_customer_fuzzy_probe_plan,
    build_progressive_plans,
    fuzzy_match_customers,
    rank_search_results,
    search_intent_signature,
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
    rows = [
        {"customer_id": f"CUST-{i}", "customer_name": f"Maya {i}"} for i in range(40)
    ]
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
    guard_context = _guard_context(production_schema)
    normalized = StrongAnchorGuard().validate(
        guard_context,
        StrongAnchorRequest(
            entity_id="order_line",
            strong_anchor_id="exact_order_line",
            anchors=(
                AnchorValue(
                    field_id="order_line_key",
                    operator="EXACT",
                    value="LINE-1",
                    value_origin="USER_MESSAGE",
                ),
            ),
        ),
    )
    assert normalized == {"order_line_key": "LINE-1"}
