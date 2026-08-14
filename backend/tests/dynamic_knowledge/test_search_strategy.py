"""Progressive search, driven by the configured identification catalogue.

Every test here runs against the *real* `config/returns/production.yaml`
catalogue resolved against the *real* active schema. That is deliberate: the
defect this replaced was seven hardcoded lists agreeing with each other and with
nothing else, so a test using a hand-written toy catalogue would prove the
machinery works while telling us nothing about whether the shipped configuration
is coherent.

Adversarial scenario #37 -- adding an identification field with no code change --
lives in `test_identification_catalogue_extensibility.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import (
    FULLTEXT_SCORE_FIELD,
    CypherCompiler,
)
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
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    MAX_CACHED_CANDIDATES,
    CustomerFulltextPolicy,
    SearchProgram,
    build_fulltext_query,
    build_search_program,
    narrow_fulltext_matches,
    rank_search_results,
    search_intent_signature,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

REPOSITORY_BACKEND = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(
        REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"
    )


@pytest.fixture(scope="module")
def catalogue(production_schema: ActiveSchema) -> IdentificationCatalogue:
    discovery = load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.discovery
    return build_identification_catalogue(
        discovery.identification_fields,
        production_schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )


def _intent(**signals: Any) -> OrderSearchIntent:
    """An intent the way the reasoning model actually sends one: extra keys.

    Constructed through `model_validate` rather than keyword arguments so these
    tests exercise the same path a model response takes. Nothing is declared on
    the class, which is the point of DISC-01.
    """
    return OrderSearchIntent.model_validate(signals)


def _program(catalogue: IdentificationCatalogue, **signals: Any) -> SearchProgram:
    return build_search_program(_intent(**signals), catalogue)


def _plans(program: SearchProgram) -> list[LogicalQueryPlan]:
    return [item.plan for item in program.primary]


def _asked(program: SearchProgram) -> set[tuple[str, str, str]]:
    return {
        (condition.entity_id, condition.field_id, condition.operator)
        for item in (*program.primary, *program.deferred)
        for condition in item.plan.filters
    }


# --- the catalogue is the source of truth -------------------------------------


def test_the_shipped_catalogue_resolves_completely_against_the_shipped_schema(
    catalogue: IdentificationCatalogue,
) -> None:
    """No configured search may name an entity, property or operator that is not there.

    This is the test that would have caught `delivered_at` surviving in code
    after the schema was rebuilt from real documents, and `CONTAINS` configured
    on an EXACT-only postal code. Both used to fail silently inside a turn, one
    plan at a time, and present to the associate as "no results".
    """
    assert catalogue.unresolved == ()


def test_every_configured_signal_is_answerable_or_says_why_not(
    catalogue: IdentificationCatalogue,
) -> None:
    """A field with no usable search is allowed -- being quiet about it is not."""
    unusable = [item.intent_key for item in catalogue.fields if not item.is_usable]

    assert unusable == ["colors"]
    described = {item["intentKey"]: item for item in catalogue.describe()}
    assert described["colors"]["searchable"] is False
    assert "unsearchableReason" in described["colors"]


# --- plan building ------------------------------------------------------------


def test_an_order_number_produces_one_exact_plan(catalogue: IdentificationCatalogue) -> None:
    program = _program(catalogue, orderNumbers=["10001"])
    plans = _plans(program)

    assert len(plans) == 1
    assert plans[0].start_entity_id == "sales_order"
    assert plans[0].filters[0].operator == "EXACT"
    assert plans[0].limit == 1


def test_partial_info_with_no_order_number_still_produces_plans(
    catalogue: IdentificationCatalogue,
) -> None:
    """A name and a rough window, which is what an associate usually has."""
    program = _program(
        catalogue, customerNames=["Smith"], dateFrom="2026-07-20", dateTo="2026-07-28"
    )
    plans = _plans(program)

    assert {plan.start_entity_id for plan in plans} == {"customer", "sales_order"}
    date_plan = next(plan for plan in plans if plan.start_entity_id == "sales_order")
    assert date_plan.filters[0].operator == "BETWEEN"
    assert date_plan.filters[0].value == {"from": "2026-07-20", "to": "2026-07-28"}


def test_two_date_bounds_are_one_window_not_two_searches(
    catalogue: IdentificationCatalogue,
) -> None:
    """Configured separately, assembled by value type rather than by field name."""
    program = _program(catalogue, dateFrom="2026-07-20", dateTo="2026-07-28")

    assert len(program.primary) == 1
    assert program.primary[0].plan.filters[0].operator == "BETWEEN"


def test_a_single_open_bound_stays_open(catalogue: IdentificationCatalogue) -> None:
    program = _program(catalogue, dateFrom="2026-07-20")

    assert [item.plan.filters[0].operator for item in program.primary] == ["GTE"]


def test_approximate_date_becomes_a_same_day_window(
    catalogue: IdentificationCatalogue,
) -> None:
    program = _program(catalogue, approximateDate="2026-07-25")

    assert len(program.primary) == 1
    condition = program.primary[0].plan.filters[0]
    assert condition.operator == "BETWEEN"
    assert condition.value == {"from": "2026-07-25", "to": "2026-07-25"}


def test_quantity_is_narrowed_by_the_product_when_both_are_given(
    catalogue: IdentificationCatalogue,
) -> None:
    program = _program(catalogue, productNames=["faucet"], quantities=[2])
    combined = next(plan for plan in _plans(program) if len(plan.filters) == 2)

    assert {condition.field_id for condition in combined.filters} == {
        "ordered_quantity",
        "product_description",
    }


def test_quantity_alone_still_searches(catalogue: IdentificationCatalogue) -> None:
    """Dropping the pass when the companion is absent would lose the signal."""
    program = _program(catalogue, quantities=[2])

    assert [condition.field_id for condition in _plans(program)[0].filters] == ["ordered_quantity"]


def test_a_complete_email_is_exact_and_a_fragment_is_loose(
    catalogue: IdentificationCatalogue,
) -> None:
    """One value, two configured searches, and the value picks which is issued.

    EXACT on a fragment finds nothing, and finding nothing looks identical to
    the customer not existing.
    """
    assert ("contact_point", "email", "EXACT") in _asked(
        _program(catalogue, emails=["dana@example.com"])
    )
    assert ("contact_point", "email", "CONTAINS") in _asked(
        _program(catalogue, emails=["dana at example"])
    )


def test_a_phone_is_tried_both_as_typed_and_as_digits(
    catalogue: IdentificationCatalogue,
) -> None:
    """Spoken and stored forms rarely agree, and neither can be assumed."""
    program = _program(catalogue, phones=["(214) 555-0142"])
    asked = {
        (condition.field_id, condition.value)
        for item in program.primary
        for condition in item.plan.filters
    }

    assert ("phone_number", "(214) 555-0142") in asked
    assert ("phone_number", "2145550142") in asked
    # The order side records its own ship-to phone and is EXACT-only, so only
    # the digits form can match there.
    assert ("ship_to_phone", "2145550142") in asked


def test_two_spellings_of_one_number_are_not_searched_twice(
    catalogue: IdentificationCatalogue,
) -> None:
    """Dedup is by the question asked, not by the string typed."""
    program = _program(catalogue, phones=["(214) 555-0142", "2145550142"])
    order_side = [
        condition
        for item in program.primary
        for condition in item.plan.filters
        if condition.field_id == "ship_to_phone"
    ]

    assert len(order_side) == 1


def test_a_city_is_searched_where_the_customer_is_and_where_the_order_went(
    catalogue: IdentificationCatalogue,
) -> None:
    """Two different questions, and "Dallas" could mean either."""
    asked = _asked(_program(catalogue, cities=["Dallas"]))

    assert ("contact_point", "city", "CONTAINS") in asked
    assert ("sales_order", "ship_to_city", "CONTAINS") in asked


def test_state_and_postal_code_use_the_operator_the_schema_allows(
    catalogue: IdentificationCatalogue,
) -> None:
    """CONTAINS on either is refused by the schema guard, losing the whole pass."""
    asked = _asked(_program(catalogue, states=["TX"], postalCodes=["75201"]))

    assert ("contact_point", "state", "EXACT") in asked
    assert ("sales_order", "ship_to_state", "EXACT") in asked
    assert ("contact_point", "postal_code", "EXACT") in asked
    assert ("sales_order", "ship_to_postal_code", "EXACT") in asked


def test_a_street_is_searched_only_where_a_street_exists(
    catalogue: IdentificationCatalogue,
) -> None:
    """`sales_order` records the ship-to city, state and ZIP but not the street."""
    program = _program(catalogue, streetAddresses=["120 Beacon St"])

    assert {plan.start_entity_id for plan in _plans(program)} == {"contact_point"}


def test_an_empty_intent_asks_for_nothing(catalogue: IdentificationCatalogue) -> None:
    program = _program(catalogue)

    assert program.primary == ()
    assert program.deferred == ()


def test_colour_is_reported_rather_than_guessed_at(
    catalogue: IdentificationCatalogue, caplog: pytest.LogCaptureFixture
) -> None:
    """Matching a colour against product_description would surface a wrong order.

    "Blue Ridge Faucet" would rank for someone who said the tap was blue, with
    no sign the colour had been matched loosely. Reporting the signal as unused
    is the honest alternative -- and an intent mixing colour with a usable
    signal must still search on the usable one, because a colour mentioned in
    the same sentence as an address is the ordinary case.
    """
    with caplog.at_level("WARNING"):
        program = _program(catalogue, streetAddresses=["18 Main Street"], colors=["blue"])

    assert ("contact_point", "address_line1", "CONTAINS") in _asked(program)
    assert program.parsed.unusable_signals == ("colors",)
    assert any("unusable" in record.message for record in caplog.records)


def test_a_signal_no_configured_field_claims_is_named_not_refused(
    catalogue: IdentificationCatalogue,
) -> None:
    """`extra="forbid"` used to reject the answer to the agent's own question.

    An unrecognized key means a stale prompt or a field nobody configured yet.
    Both are worth reporting; neither is worth failing the associate's turn for.
    """
    program = _program(catalogue, orderNumbers=["10001"], favouriteColour=["blue"])

    assert program.parsed.unknown_keys == ("favouriteColour",)
    assert len(program.primary) == 1


# --- guard + compiler round trip against the production schema ----------------


def test_every_plan_the_catalogue_produces_passes_the_guard_and_compiles(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The whole catalogue exercised at once, against the real guard rails.

    Configuration that cannot be executed is worse than code that cannot: it
    looks changeable and is not. This is what makes the catalogue's promise --
    "add a field and it works" -- checkable rather than asserted.
    """
    program = _program(
        catalogue,
        orderNumbers=["10001"],
        orderIds=["10001"],
        customerNames=["Smith"],
        emails=["dana@example.com"],
        phones=["(214) 555-0142"],
        streetAddresses=["120 Beacon St"],
        cities=["Dallas"],
        states=["TX"],
        postalCodes=["75201"],
        skus=["12345"],
        productNames=["faucet"],
        quantities=[2],
        dateFrom="2026-07-20",
        dateTo="2026-07-28",
        freeTextTerms=["ORD-10001"],
    )
    assert len(program.primary) >= 15
    assert len(program.deferred) == 1

    guard_context = _guard_context(production_schema)
    compiler = CypherCompiler()
    query_safety = QuerySafetyGuard(QuerySafetyPolicy())
    for item in (*program.primary, *program.deferred):
        SchemaQueryGuard().validate(guard_context, item.plan)
        query_safety.validate(item.plan)
        assert compiler.compile_read(production_schema, item.plan).read_only is True


# --- the pagination signature -------------------------------------------------


def test_the_signature_ignores_metadata_but_not_identifying_signals(
    catalogue: IdentificationCatalogue,
) -> None:
    base = _intent(customerNames=["Maya"])
    next_page = _intent(customerNames=["Maya"], wantsMoreResults=True, confidence=0.9)
    different = _intent(customerNames=["John"])

    assert search_intent_signature(base, catalogue) == search_intent_signature(next_page, catalogue)
    assert search_intent_signature(base, catalogue) != search_intent_signature(different, catalogue)


def test_the_signature_moves_when_a_contact_detail_changes(
    catalogue: IdentificationCatalogue,
) -> None:
    """Otherwise a "show next" pages through someone else's cached results."""
    assert search_intent_signature(
        _intent(emails=["dana@example.com"]), catalogue
    ) != search_intent_signature(_intent(emails=["sam@example.com"]), catalogue)
    assert search_intent_signature(
        _intent(phones=["2145550142"]), catalogue
    ) != search_intent_signature(_intent(phones=["2145550143"]), catalogue)


def test_an_unrecognized_signal_still_changes_the_signature(
    catalogue: IdentificationCatalogue,
) -> None:
    """Two searches differing only in a key nobody configured are still two searches."""
    assert search_intent_signature(
        _intent(customerNames=["Maya"]), catalogue
    ) != search_intent_signature(_intent(customerNames=["Maya"], mysterySignal=["x"]), catalogue)


# --- ranking ------------------------------------------------------------------


def _ranked(
    catalogue: IdentificationCatalogue, rows: list[dict[str, Any]], **signals: Any
) -> list[dict[str, Any]]:
    intent = _intent(**signals)
    program = build_search_program(intent, catalogue)
    result = rank_search_results(intent, [{"rows": rows, "count": len(rows)}], program=program)
    return list(result["candidates"])


def test_rank_reads_the_rows_key_the_gateway_actually_returns(
    catalogue: IdentificationCatalogue,
) -> None:
    ranked = _ranked(
        catalogue,
        [{"sales_order_number": "10001", "customer_id": "C1"}],
        orderNumbers=["10001"],
    )

    assert ranked[0]["data"]["sales_order_number"] == "10001"
    assert "sales_order_number_exact" in ranked[0]["matches"]


def test_rank_merges_rows_for_the_same_order_across_plans(
    catalogue: IdentificationCatalogue,
) -> None:
    intent = _intent(customerNames=["Smith"], productNames=["faucet"])
    program = build_search_program(intent, catalogue)
    result = rank_search_results(
        intent,
        [
            {"rows": [{"sales_order_number": "10001", "customer_name": "John Smith"}]},
            {"rows": [{"sales_order_number": "10001", "product_description": "Moen faucet"}]},
        ],
        program=program,
    )

    assert result["total_found"] == 1
    merged = result["candidates"][0]["data"]
    assert merged["customer_name"] == "John Smith"
    assert merged["product_description"] == "Moen faucet"


def test_rank_caps_cached_candidates_but_reports_the_true_total(
    catalogue: IdentificationCatalogue,
) -> None:
    """Pagination depends on total_found staying accurate past the cache bound."""
    intent = _intent(customerNames=["Maya"])
    program = build_search_program(intent, catalogue)
    rows = [
        {"customer_id": f"CUST-{index}", "customer_name": f"Maya {index}"} for index in range(40)
    ]
    result = rank_search_results(intent, [{"rows": rows}], program=program)

    assert len(result["candidates"]) == MAX_CACHED_CANDIDATES
    assert result["total_found"] == 40


def test_an_exact_email_outranks_a_shared_location(
    catalogue: IdentificationCatalogue,
) -> None:
    """An email identifies one customer; a city narrows to a thousand.

    The ordering now comes from `ranking_weight_millionths` in configuration
    rather than from numbers written into the ranker, and it has to survive the
    move -- an operator who never touches the catalogue must see what they saw.
    """
    ranked = _ranked(
        catalogue,
        [
            {"customer_id": "C-CITY", "city": "Dallas"},
            {"customer_id": "C-EMAIL", "email": "dana@example.com"},
        ],
        emails=["dana@example.com"],
        cities=["Dallas"],
    )

    assert [candidate["candidate_id"] for candidate in ranked] == ["C-EMAIL", "C-CITY"]
    assert "email_exact" in ranked[0]["matches"]


def test_a_phone_matches_across_punctuation(catalogue: IdentificationCatalogue) -> None:
    """ "(214) 555-0142" and a stored "2145550142" are the same number.

    Configured `normalization: DIGITS` is what makes that true, rather than a
    digits comparison hand-written into the phone branch of the ranker.
    """
    ranked = _ranked(
        catalogue,
        [{"customer_id": "C1", "phone_number": "2145550142"}],
        phones=["(214) 555-0142"],
    )

    assert "phone_number_exact" in ranked[0]["matches"]


def test_a_country_code_is_a_probable_match_not_an_exact_one(
    catalogue: IdentificationCatalogue,
) -> None:
    """ "+1 214..." contains the number but is not literally it.

    Claiming exactness would let a different national number ending in the same
    digits present as certain.
    """
    ranked = _ranked(
        catalogue,
        [{"customer_id": "C1", "phone_number": "+1 214-555-0142"}],
        phones=["2145550142"],
    )

    assert ranked[0]["matches"] == ["phone_number_contains"]


def test_a_state_barely_moves_the_ranking(catalogue: IdentificationCatalogue) -> None:
    """It narrows a result set; it does not identify anybody."""
    ranked = _ranked(
        catalogue,
        [
            {"customer_id": "C-STATE", "state": "TX"},
            {"customer_id": "C-NAME", "customer_name": "Acme Plumbing"},
        ],
        states=["TX"],
        customerNames=["Acme"],
    )

    assert [candidate["candidate_id"] for candidate in ranked] == ["C-NAME", "C-STATE"]


def test_a_date_window_match_is_scored(catalogue: IdentificationCatalogue) -> None:
    """The graph applied the range, so the row being present is the match."""
    ranked = _ranked(
        catalogue,
        [{"sales_order_number": "SO-1", "order_date": "2026-08-14"}],
        dateFrom="2026-08-01",
        dateTo="2026-08-31",
    )

    assert "order_date_between" in ranked[0]["matches"]


def test_ranking_reports_what_it_could_not_use(catalogue: IdentificationCatalogue) -> None:
    intent = _intent(orderNumbers=["10001"], colors=["blue"], somethingElse=["x"])
    program = build_search_program(intent, catalogue)
    result = rank_search_results(intent, [], program=program)

    assert result["unsupported_signals"] == ["colors"]
    assert result["unrecognized_signals"] == ["somethingElse"]


# --- the indexed customer-name search (SRCH-01, now catalogue-configured) -----


def test_the_misspelling_search_is_deferred_until_everything_else_fails(
    catalogue: IdentificationCatalogue,
) -> None:
    """It must not run alongside the exact passes and dilute them."""
    program = _program(catalogue, customerNames=["Jhon Smi"])

    assert [item.plan.operation for item in program.primary] == [QueryOperation.SEARCH]
    assert [item.plan.operation for item in program.deferred] == [QueryOperation.FULLTEXT_SEARCH]
    assert program.deferred[0].search.label == "customer_name_fuzzy"


def test_the_indexed_plan_bounds_returned_rows_and_never_the_corpus(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The SRCH-01 invariant, carried through the move into configuration.

    The predecessor of this test asserted `limit == FUZZY_CUSTOMER_PROBE_LIMIT`
    on an unfiltered, unordered `MATCH (c:Customer) ... LIMIT 100`. What has to
    be true instead is that the limit applies *after* the server has ranked
    every customer the index covers.
    """
    program = _program(catalogue, customerNames=["Jhon Smi"])
    plan = program.deferred[0].plan

    assert plan.operation is QueryOperation.FULLTEXT_SEARCH
    assert plan.filters == ()

    SchemaQueryGuard().validate(_guard_context(production_schema), plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    compiled = CypherCompiler().compile_read(production_schema, plan)

    assert compiled.cypher.startswith("CALL db.index.fulltext.queryNodes(")
    assert "ORDER BY score DESC" in compiled.cypher
    # The index name is a parameter, never interpolated.
    assert plan.fulltext_index not in compiled.cypher
    assert compiled.parameters["fulltext_index"] == "customer_name_search_v2"


def test_the_index_name_is_configuration(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """A rebuilt index under a new name is reachable without a release."""
    discovery = load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.discovery
    repointed = build_identification_catalogue(
        discovery.identification_fields,
        production_schema,
        default_fulltext_index="customer_name_search_v3",
    )
    program = _program(repointed, customerNames=["Jhon Smi"])

    assert program.deferred[0].plan.fulltext_index == "customer_name_search_v3"


def test_a_full_text_plan_is_refused_for_a_field_this_role_cannot_search(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The index replaces the WHERE clause, so nothing else validates the field."""
    plan = _program(catalogue, customerNames=["Jhon Smi"]).deferred[0].plan

    with pytest.raises(GuardRejected) as error:
        SchemaQueryGuard().validate(
            _guard_context(production_schema),
            plan.model_copy(update={"fulltext_field_id": "no_such_field"}),
        )

    assert error.value.code == "REJECT_INVALID_SCHEMA_REFERENCE"


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
    """Parity with the difflib fallback that SRCH-01 removed.

    Term generation is what a unit test can prove. That these terms find the row
    in a corpus far larger than any window is proven against a real index in
    test_order_discovery_fulltext_real_infra.py.
    """
    query = build_fulltext_query((typed,), CustomerFulltextPolicy())

    for term in expected_terms:
        assert term in query


def test_a_short_token_is_a_prefix_only() -> None:
    """A fuzzy term that matches everything ranks nothing."""
    assert build_fulltext_query(("Ace",), CustomerFulltextPolicy()) == "Ace*"


def test_lucene_syntax_in_a_value_reaches_the_index_as_text_or_not_at_all() -> None:
    """The value is associate-supplied and goes into a query language."""
    query = build_fulltext_query(('Smith~10 OR *:* AND "x"',), CustomerFulltextPolicy())

    assert query == "(Smith* OR Smith~1) AND 10*"
    assert ":" not in query and '"' not in query and "~10" not in query


def _scored_row(customer_id: str, score: float) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "customer_name": f"Customer {customer_id}",
        FULLTEXT_SCORE_FIELD: score,
    }


def test_narrowing_is_bounded_by_score_and_not_by_row_count() -> None:
    """Five rows is the right answer when five customers are plausible."""
    narrowed = narrow_fulltext_matches(
        [
            _scored_row("C1", 9.0),
            _scored_row("C2", 8.2),
            _scored_row("C3", 1.1),
            _scored_row("C4", 0.4),
        ],
        policy=CustomerFulltextPolicy(),
    )

    assert [row["customer_id"] for row, _ in narrowed] == ["C1", "C2"]


def test_narrowing_keeps_every_row_that_is_genuinely_competitive() -> None:
    narrowed = narrow_fulltext_matches(
        [_scored_row(f"C{index}", 9.0) for index in range(8)], policy=CustomerFulltextPolicy()
    )

    assert len(narrowed) == 8


def test_the_relevance_score_does_not_travel_on_as_customer_data() -> None:
    """Left in the row it reaches the model's context and the stored evidence."""
    ((row, score),) = narrow_fulltext_matches(
        [_scored_row("C1", 4.5)], policy=CustomerFulltextPolicy()
    )

    assert FULLTEXT_SCORE_FIELD not in row
    assert score == 4.5


def test_rows_without_a_usable_score_are_dropped_rather_than_ranked_first() -> None:
    narrowed = narrow_fulltext_matches(
        [{"customer_id": "C1", "customer_name": "Acme"}, _scored_row("C2", 3.0)],
        policy=CustomerFulltextPolicy(),
    )

    assert [row["customer_id"] for row, _ in narrowed] == ["C2"]


def test_nothing_returned_narrows_to_nothing() -> None:
    assert narrow_fulltext_matches([], policy=CustomerFulltextPolicy()) == []


# --- strong anchors on the production schema ----------------------------------


def test_combination_anchor_requires_both_fields(production_schema: ActiveSchema) -> None:
    with pytest.raises(GuardRejected) as error:
        StrongAnchorGuard().validate(
            _guard_context(production_schema),
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
    normalized = StrongAnchorGuard().validate(
        _guard_context(production_schema),
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


def test_order_line_has_a_composite_strong_anchor(production_schema: ActiveSchema) -> None:
    # A line is identified by its branch, its order and its line number -- the
    # single synthetic `order_line_key` it used to name does not exist in the
    # salesInv documents.
    normalized = StrongAnchorGuard().validate(
        _guard_context(production_schema),
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

    An anchor whose fields are not `on_demand_sync_anchor` is declared but dead:
    the guard rejects every request against it, so the escalation it exists to
    enable can never run, and the only symptom is a sync that silently never
    happens.
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
