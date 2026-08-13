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
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import FULLTEXT_SCORE_FIELD
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    MAX_CACHED_CANDIDATES,
    CustomerFulltextPolicy,
    build_customer_fulltext_plan,
    build_customer_name_query,
    build_progressive_plans,
    narrow_fulltext_matches,
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


def test_an_unsupported_signal_is_logged_and_does_not_take_the_rest_of_the_search_with_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Colour is the only signal with no backing field, and it must cost nothing.

    Two failures are in scope. Dropping the colour without a word makes an empty
    result indistinguishable from "no such order", so the WARNING is what makes
    it diagnosable. And an intent that mixes an unsupported signal with a
    supported one must still search on the supported one -- an address given in
    the same sentence as a colour is the ordinary case, and answering it with
    nothing because of the colour would be the worse bug of the two.
    """
    intent = OrderSearchIntent(streetAddresses=("18 Main Street",), colors=("blue",))
    with caplog.at_level("WARNING"):
        plans = build_progressive_plans(intent)

    assert ("contact_point", "address_line1", "CONTAINS") in _fields_asked(plans)
    assert unsupported_signals(intent) == ("colors",)
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


# --- the indexed customer-name query ------------------------------------------
#
# What replaced an unfiltered `LIMIT 100` read scored with difflib. The tests
# below can prove the query asked and the narrowing applied to what comes back;
# only a real index over a real corpus can prove the row is *found*, which is
# what test_order_discovery_fulltext_real_infra.py exists for.


def test_a_misspelt_token_is_asked_for_as_a_fuzzy_term() -> None:
    """The whole point of the index: "Jhon" has to be able to match "John".

    A prefix alone cannot -- the first letter that differs ends the match -- so
    an abbreviation and a misspelling need different terms, OR'd, per token.
    """
    query = build_customer_name_query(("Jhon Smi",), CustomerFulltextPolicy())

    assert query == "(Jhon* OR Jhon~1) AND Smi*"


def test_a_short_token_is_a_prefix_only() -> None:
    """Two edits on a three-letter token reaches most three-letter tokens.

    A fuzzy term that matches everything ranks nothing, which is the failure
    mode that looks like the search working.
    """
    assert build_customer_name_query(("Ace",), CustomerFulltextPolicy()) == "Ace*"


def test_the_edit_distance_thresholds_come_from_the_policy() -> None:
    """An operator tightening the misspelling policy has to change the query."""
    strict = CustomerFulltextPolicy(max_edit_distance=1, two_edit_min_token_length=8)

    assert build_customer_name_query(("Zephyrine",), strict) == "(Zephyrine* OR Zephyrine~1)"
    assert (
        build_customer_name_query(("Zephyrine",), CustomerFulltextPolicy())
        == "(Zephyrine* OR Zephyrine~2)"
    )


def test_lucene_syntax_in_a_name_reaches_the_index_as_text_or_not_at_all() -> None:
    """The name is associate-supplied and goes into a query language.

    Tokenizing on `[A-Za-z0-9]+` is the guard: every operator Lucene understands
    is dropped before the string is assembled, so `Smith~10 OR *:*` cannot widen
    the search to the whole index.
    """
    query = build_customer_name_query(('Smith~10 OR *:* AND "x"',), CustomerFulltextPolicy())

    assert query == "(Smith* OR Smith~1) AND 10*"
    assert ":" not in query and '"' not in query and "~10" not in query


def test_two_names_are_alternatives_and_one_name_is_a_conjunction() -> None:
    """Two spellings of one customer are alternatives; two words are not.

    AND'ing across separate names would require a customer to be called both
    things at once; OR'ing within one name would let a common surname alone
    drag in every customer sharing it.
    """
    policy = CustomerFulltextPolicy()

    assert build_customer_name_query(("Acme Plumbing", "Akme"), policy) == (
        "((Acme* OR Acme~1) AND (Plumbing* OR Plumbing~2)) OR ((Akme* OR Akme~1))"
    )


def test_a_name_with_nothing_searchable_in_it_produces_no_plan() -> None:
    """"customer" and "order" are the model's words, not the customer's name.

    A query built from them alone would match on the noise; returning no plan
    reports "no misspelling recovery available" instead.
    """
    assert build_customer_name_query(("the customer",), CustomerFulltextPolicy()) == ""
    assert build_customer_fulltext_plan(("the customer",), CustomerFulltextPolicy()) is None


def test_a_disabled_policy_produces_no_plan() -> None:
    assert (
        build_customer_fulltext_plan(("Jhon Smi",), CustomerFulltextPolicy(enabled=False)) is None
    )


def test_the_indexed_plan_bounds_returned_rows_and_never_the_corpus(
    production_schema: ActiveSchema,
) -> None:
    """The invariant this whole path exists to restore.

    The predecessor of this test asserted `limit == FUZZY_CUSTOMER_PROBE_LIMIT`
    on an unfiltered, unordered `MATCH (c:Customer) ... LIMIT 100` — it pinned a
    P0 defect as intended behaviour. What has to be true instead is that the
    limit applies *after* the server has ranked every customer the index covers:
    the query is a full-text call, the ordering is not optional, and no WHERE
    clause narrows the set before the index has scored it.
    """
    policy = CustomerFulltextPolicy()
    plan = build_customer_fulltext_plan(("Jhon Smi",), policy)
    assert plan is not None
    assert plan.operation is QueryOperation.FULLTEXT_SEARCH
    assert plan.fulltext_index == policy.index_name
    assert plan.filters == ()

    guard_context = _guard_context(production_schema)
    SchemaQueryGuard().validate(guard_context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    compiled = CypherCompiler().compile_read(production_schema, plan)

    assert compiled.read_only is True
    assert compiled.cypher.startswith("CALL db.index.fulltext.queryNodes(")
    assert "ORDER BY score DESC" in compiled.cypher
    # The index name is a parameter, never interpolated: an operator repoints it
    # in configuration and no Cypher is rewritten.
    assert policy.index_name not in compiled.cypher
    assert compiled.parameters["fulltext_index"] == policy.index_name


def test_the_index_name_is_not_a_constant(production_schema: ActiveSchema) -> None:
    """A rebuilt index under a new name must be reachable by configuration alone."""
    plan = build_customer_fulltext_plan(
        ("Jhon Smi",), CustomerFulltextPolicy(index_name="customer_name_search_v3")
    )
    assert plan is not None
    compiled = CypherCompiler().compile_read(production_schema, plan)

    assert compiled.parameters["fulltext_index"] == "customer_name_search_v3"


def test_a_full_text_plan_is_refused_for_a_field_this_role_cannot_search(
    production_schema: ActiveSchema,
) -> None:
    """The index replaces the WHERE clause, so nothing else validates the field.

    Without the guard branch, a full-text plan would be the one way to rank on a
    field a principal is not permitted to search — and ranking on it is already
    the disclosure, whatever the result columns say.
    """
    plan = build_customer_fulltext_plan(("Jhon Smi",), CustomerFulltextPolicy())
    assert plan is not None
    stranger = GuardContext(
        schema=production_schema,
        agent_policy=production_schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )
    with pytest.raises(GuardRejected) as error:
        SchemaQueryGuard().validate(
            stranger, plan.model_copy(update={"fulltext_field_id": "no_such_field"})
        )

    assert error.value.code == "REJECT_INVALID_SCHEMA_REFERENCE"


# --- narrow_fulltext_matches --------------------------------------------------


def _row(customer_id: str, score: float) -> dict[str, object]:
    return {
        "customer_id": customer_id,
        "customer_name": f"Customer {customer_id}",
        FULLTEXT_SCORE_FIELD: score,
    }


def test_narrowing_is_bounded_by_score_and_not_by_row_count() -> None:
    """Five rows is the right answer when five customers are plausible.

    When one match is clearly better than the rest, padding the list out to a
    fixed size is how a wrong candidate gets shown next to the right one as
    though the search were undecided.
    """
    narrowed = narrow_fulltext_matches(
        [_row("C1", 9.0), _row("C2", 8.2), _row("C3", 1.1), _row("C4", 0.4)],
        policy=CustomerFulltextPolicy(),
    )

    assert [row["customer_id"] for row, _ in narrowed] == ["C1", "C2"]


def test_narrowing_keeps_every_row_that_is_genuinely_competitive() -> None:
    """No fixed cap: a name shared by eight customers returns eight candidates."""
    narrowed = narrow_fulltext_matches(
        [_row(f"C{index}", 9.0) for index in range(8)], policy=CustomerFulltextPolicy()
    )

    assert len(narrowed) == 8


def test_the_relevance_score_does_not_travel_on_as_customer_data() -> None:
    """It is search metadata. Left in the row it reaches the model's context and
    the stored evidence as though the graph had returned it as a property."""
    (row, score), = narrow_fulltext_matches([_row("C1", 4.5)], policy=CustomerFulltextPolicy())

    assert FULLTEXT_SCORE_FIELD not in row
    assert score == 4.5


def test_rows_without_a_usable_score_are_dropped_rather_than_ranked_first() -> None:
    """A missing score cannot be read as zero or as infinity, so it is read as
    "this row did not come from the index" and left out."""
    narrowed = narrow_fulltext_matches(
        [{"customer_id": "C1", "customer_name": "Acme"}, _row("C2", 3.0)],
        policy=CustomerFulltextPolicy(),
    )

    assert [row["customer_id"] for row, _ in narrowed] == ["C2"]


def test_nothing_returned_narrows_to_nothing() -> None:
    assert narrow_fulltext_matches([], policy=CustomerFulltextPolicy()) == []


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


@pytest.mark.parametrize(
    ("typed", "expected_terms"),
    (
        ("melgan heatng", ("melgan~1", "heatng~1")),
        ("Jhon Smith", ("Jhon~1", "Smith~1")),
        ("Zephyrine Okonkwo", ("Zephyrine~2", "Okonkwo~1")),
    ),
)
def test_the_misspellings_the_old_probe_handled_are_still_asked_for(
    typed: str, expected_terms: tuple[str, ...]
) -> None:
    """Parity with the difflib fallback that was removed.

    "MELGON HEATING & COOLING" against "melgan heatng" was the case that
    justified the client-side scorer: one edit in the name, one in the second
    word, and a trade-name suffix nobody types. Each of these has to survive as
    a fuzzy term with at least the edit budget the misspelling needs -- the
    untyped suffix costs nothing now, because the index matches per token
    instead of comparing whole strings.

    Term generation is what a unit test can prove. That these terms find the row
    in a corpus far larger than any window is proven against a real index in
    test_order_discovery_fulltext_real_infra.py.
    """
    query = build_customer_name_query((typed,), CustomerFulltextPolicy())

    for term in expected_terms:
        assert term in query


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


# ---------------------------------------------------------------------------
# Ranking the contact signals
#
# Every row a plan returns became a candidate at the base score, so a contact
# matched on a ZIP shared by a thousand customers ranked level with one matched
# on the exact email the associate read out.
# ---------------------------------------------------------------------------


def _ranked(intent: OrderSearchIntent, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result = rank_search_results(intent, [{"rows": rows, "count": len(rows)}])
    return list(result["candidates"])


def test_an_exact_email_outranks_a_shared_location() -> None:
    """An email identifies one customer; a city narrows to a thousand."""
    intent = OrderSearchIntent(emails=("dana@example.com",), cities=("Dallas",))
    ranked = _ranked(
        intent,
        [
            {"customer_id": "C-CITY", "city": "Dallas"},
            {"customer_id": "C-EMAIL", "email": "dana@example.com"},
        ],
    )

    assert [candidate["candidate_id"] for candidate in ranked] == ["C-EMAIL", "C-CITY"]
    assert "email_exact" in ranked[0]["matches"]


def test_a_phone_matches_across_punctuation() -> None:
    """ "(214) 555-0142" and a stored "2145550142" are the same number.

    Scoring them as a miss is how the right customer ends up below the wrong
    one, on the signal the associate was specifically asked for.
    """
    ranked = _ranked(
        OrderSearchIntent(phones=("(214) 555-0142",)),
        [{"customer_id": "C1", "phone_number": "2145550142"}],
    )

    assert "phone_exact" in ranked[0]["matches"]


def test_a_country_code_is_a_probable_match_not_an_exact_one() -> None:
    """ "+1 214..." contains the number but is not literally it.

    Claiming exactness here would let a different national number that happens
    to end in the same digits present as certain.
    """
    ranked = _ranked(
        OrderSearchIntent(phones=("2145550142",)),
        [{"customer_id": "C1", "phone_number": "+1 214-555-0142"}],
    )

    assert ranked[0]["matches"] == ["phone_contains"]


def test_a_date_window_match_is_scored_again() -> None:
    """The branch scored `delivered_at`, which the schema no longer has.

    Every date-window search since the schema was rebuilt from real documents
    narrowed the plan set and then contributed nothing to the ranking.
    """
    ranked = _ranked(
        OrderSearchIntent(dateFrom="2026-08-01", dateTo="2026-08-31"),
        [{"sales_order_number": "SO-1", "order_date": "2026-08-14"}],
    )

    assert "order_date_in_range" in ranked[0]["matches"]


def test_a_state_barely_moves_the_ranking() -> None:
    """It narrows a result set; it does not identify anybody.

    A state that outranked a name would reorder a search around the least
    specific thing the associate said.
    """
    intent = OrderSearchIntent(states=("TX",), customerNames=("Acme",))
    ranked = _ranked(
        intent,
        [
            {"customer_id": "C-STATE", "state": "TX"},
            {"customer_id": "C-NAME", "customer_name": "Acme Plumbing"},
        ],
    )

    assert [candidate["candidate_id"] for candidate in ranked] == ["C-NAME", "C-STATE"]
