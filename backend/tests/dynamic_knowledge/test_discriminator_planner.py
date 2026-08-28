"""What to ask next, decided by the evidence rather than by a fixed order.

The behaviour under test is the one the audit names: *current facts -> enabled
searchable fields -> selectivity -> evidence strength -> next best discriminator
-> execute -> observe result count -> re-evaluate*. What makes it a planner
rather than a questionnaire is that the same catalogue produces different
questions for different conversations, so most of these tests hold the
configuration still and vary only the evidence.

The shipped catalogue is used throughout. Selectivity is exercised on a schema
whose fields carry a real `FieldSelectivity`, because the shipped descriptor is
hand-authored and carries none -- which is itself a case worth asserting: the
planner must fall back to the configured order and *say* that is what it did,
rather than reporting a measurement it does not have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.planner import (
    DiscriminatorBasis,
    order_searches_by_discrimination,
    rank_discriminators,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import build_search_program
from return_platform.dynamic_knowledge.schema import ActiveSchema

REPOSITORY_BACKEND = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(
        REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"
    )


def _catalogue(schema: ActiveSchema) -> IdentificationCatalogue:
    discovery = load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.discovery
    return build_identification_catalogue(
        discovery.identification_fields,
        schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


@pytest.fixture(scope="module")
def catalogue(production_schema: ActiveSchema) -> IdentificationCatalogue:
    return _catalogue(production_schema)


def _profiled(
    schema: ActiveSchema, measurements: dict[tuple[str, str], dict[str, Any]]
) -> ActiveSchema:
    """The same schema with `FieldSelectivity` attached to named properties.

    Built by re-validating a dump rather than by constructing a toy schema, so
    the planner is still ranking the real entities and the real catalogue binds
    to them unchanged.
    """
    payload = schema.model_dump(mode="json")
    for (entity_id, field_id), selectivity in measurements.items():
        payload["entities"][entity_id]["fields"][field_id]["selectivity"] = selectivity
    return ActiveSchema.model_validate(payload)


def _parsed(catalogue: IdentificationCatalogue, **signals: Any) -> Any:
    return catalogue.parse(OrderSearchIntent.model_validate(signals).signal_values)


def _candidate(**data: Any) -> dict[str, Any]:
    return {"candidate_id": data.get("customer_id", "x"), "data": data, "score": 0.5, "matches": []}


# --- the fallback, which is the shipped situation ------------------------------


def test_an_unprofiled_field_is_ranked_by_configured_order_and_says_so(
    catalogue: IdentificationCatalogue,
) -> None:
    """The shipped descriptor carries no selectivity at all.

    `FieldSelectivity` is explicit that UNKNOWN means nothing profiled the field,
    and that a consumer treating it as low selectivity would be ranking on the
    absence of evidence. So the fallback is the configured question order, and
    the basis says which of the two a reader is looking at.
    """
    ranked = rank_discriminators(catalogue, _parsed(catalogue))

    assert ranked
    assert {item.basis for item in ranked} == {DiscriminatorBasis.CONFIGURED.value}
    assert "not profiled" in ranked[0].reason
    # Highest configured priority first -- read from the catalogue rather than
    # named, because which field that is, is the operator's to decide. It was
    # `orderNumbers` at 120 until the release demoted asking for paperwork below
    # every detail an associate can answer from memory; pinning the name here
    # made a configuration change look like a ranking defect.
    expected = max(
        (field for field in catalogue.fields if field.intent_key == ranked[0].intent_key),
        key=lambda field: field.clarification_priority,
    )
    assert all(
        expected.clarification_priority >= field.clarification_priority
        for field in catalogue.fields
        if field.intent_key in {item.intent_key for item in ranked}
    )


def test_a_measured_field_outranks_a_higher_priority_unprofiled_one(
    production_schema: ActiveSchema,
) -> None:
    """Measurement beats the configured guess, which is the point of measuring.

    `postal_code` sits at priority 75, well below the order number at 120. Told
    that it is nearly unique in this dataset, the planner asks for it first --
    without anyone editing the configured order.
    """
    catalogue = _catalogue(
        _profiled(
            production_schema,
            {
                ("contact_point", "postal_code"): {
                    "sampled_rows": 5000,
                    "approximate_row_count": 250000,
                    "null_rate": 0.0,
                    "approximate_distinct": 4900,
                    "identifier_likelihood": "LIKELY",
                }
            },
        )
    )
    ranked = rank_discriminators(catalogue, _parsed(catalogue))

    assert ranked[0].intent_key == "postalCodes"
    assert ranked[0].basis == DiscriminatorBasis.MEASURED.value
    assert ranked[0].score > 0.9


def test_a_declared_unique_field_is_ranked_above_any_measurement(
    production_schema: ActiveSchema,
) -> None:
    """A unique index is a statement about every row, not about a sample."""
    catalogue = _catalogue(
        _profiled(
            production_schema,
            {
                ("sales_order", "sales_order_number"): {
                    "sampled_rows": 50,
                    "null_rate": 0.0,
                    "approximate_distinct": 50,
                    "identifier_likelihood": "DECLARED_UNIQUE",
                }
            },
        )
    )
    ranked = rank_discriminators(catalogue, _parsed(catalogue))

    assert ranked[0].intent_key in {"orderNumbers", "orderIds"}
    assert ranked[0].basis == DiscriminatorBasis.DECLARED_UNIQUE.value
    assert ranked[0].score == 1.0


# --- evidence strength: the anti-questionnaire mechanism ----------------------


def test_a_field_every_candidate_agrees_on_is_not_worth_asking_for(
    catalogue: IdentificationCatalogue,
) -> None:
    """The heart of DISC-03.

    Four candidates, all in Dallas, all with different names. A fixed order
    would ask for the city because the city sits where it sits; asking it
    removes no candidate and costs the associate a turn. The planner ranks it at
    zero *for this conversation* and says why.
    """
    candidates = [
        _candidate(customer_id=f"C{index}", city="Dallas", customer_name=name)
        for index, name in enumerate(("Foster", "Ortiz", "Garcia", "Nair"))
    ]
    ranked = rank_discriminators(
        catalogue,
        _parsed(catalogue, customerNames=["Maya"]),
        candidates=candidates,
        result_count=len(candidates),
        limit=50,
    )
    by_key = {item.intent_key: item for item in ranked}

    assert by_key["cities"].score == 0.0
    assert "every remaining candidate has the same value" in by_key["cities"].reason


def test_the_same_field_is_worth_asking_for_when_the_candidates_differ(
    catalogue: IdentificationCatalogue,
) -> None:
    """Same catalogue, same field, different evidence, opposite answer.

    This is what separates a planner from a question order: nothing about the
    configuration changed between this test and the one above.
    """
    candidates = [
        _candidate(customer_id=f"C{index}", city=city)
        for index, city in enumerate(("Dallas", "Austin", "Houston", "El Paso"))
    ]
    ranked = rank_discriminators(
        catalogue,
        _parsed(catalogue, customerNames=["Maya"]),
        candidates=candidates,
        result_count=len(candidates),
        limit=50,
    )
    by_key = {item.intent_key: item for item in ranked}

    assert by_key["cities"].score > 0.0
    assert by_key["cities"].splits_candidates == 4
    assert "splits the 4 candidates into 4" in by_key["cities"].reason


def test_a_partial_answer_is_still_worth_asking_for_when_the_candidates_differ_on_it(
    catalogue: IdentificationCatalogue,
) -> None:
    """A prefix is not an answer, and the candidates say which it was.

    "find order for BOYLE" against six customers who share a surname and differ
    by first name: the field was in `answered`, so it was excluded before the
    candidates were ever consulted, and the ranking offered an email address
    instead of the one question that finishes the name. An associate reading
    that reasonably concludes the agent cannot tell a half name from a whole one.

    Being asked is the whole assertion. The score matters less than the field
    appearing at all -- exclusion happened before any measurement.
    """
    candidates = [
        _candidate(customer_id=f"C{index}", customer_name=f"{first} BOYLE")
        for index, first in enumerate(("RANDALL", "VIRGINIA", "DENNIS", "JESSICA"))
    ]
    ranked = rank_discriminators(
        catalogue,
        _parsed(catalogue, customerNames=["BOYLE"]),
        candidates=candidates,
        result_count=len(candidates),
        limit=50,
    )
    by_key = {item.intent_key: item for item in ranked}

    assert "customerNames" in by_key, "a half-given name is excluded as though it were answered"
    assert by_key["customerNames"].splits_candidates == 4
    assert "partial value rather than an answer" in by_key["customerNames"].reason


def test_an_answered_field_every_candidate_agrees_on_stays_out_of_the_ranking(
    catalogue: IdentificationCatalogue,
) -> None:
    """The other half, and the reason the rule is narrow.

    Five accounts that all read the same customer name: the name IS settled, and
    re-asking it narrows nothing. This is the case the exclusion was written for,
    and it must survive the case above.
    """
    candidates = [
        _candidate(customer_id=f"C{index}", customer_name="STONEBRIDGE PIPEWORKS")
        for index in range(5)
    ]
    ranked = rank_discriminators(
        catalogue,
        _parsed(catalogue, customerNames=["STONEBRIDGE"]),
        candidates=candidates,
        result_count=len(candidates),
        limit=50,
    )

    assert "customerNames" not in {item.intent_key for item in ranked}


def test_a_field_no_candidate_carries_is_unknown_rather_than_useless(
    catalogue: IdentificationCatalogue,
) -> None:
    """Absence of evidence again, one level down.

    Ranking "no candidate mentions a ZIP" the same as "every candidate has the
    same ZIP" would quietly stop the agent ever asking for a field the search
    results happen not to project.
    """
    candidates = [
        _candidate(customer_id="C1", city="Dallas"),
        _candidate(customer_id="C2", city="Austin"),
    ]
    ranked = rank_discriminators(
        catalogue,
        _parsed(catalogue, customerNames=["Maya"]),
        candidates=candidates,
        result_count=len(candidates),
        limit=50,
    )
    by_key = {item.intent_key: item for item in ranked}

    assert by_key["postalCodes"].splits_candidates is None
    assert by_key["postalCodes"].score > 0.0
    assert "no candidate carries this field" in by_key["postalCodes"].reason


# --- observing the result count ----------------------------------------------


def test_zero_results_asks_for_an_independent_anchor(
    catalogue: IdentificationCatalogue,
) -> None:
    """Narrowing an empty set narrows nothing.

    When the signals gathered so far found nothing, the next question has to be
    able to find the order on its own rather than refine a result set that does
    not exist.
    """
    ranked = rank_discriminators(
        catalogue, _parsed(catalogue, customerNames=["Zephyrine"]), candidates=(), result_count=0
    )

    assert "an independent anchor is worth more" in ranked[0].reason
    # An anchor, and the best-ranked one the operator offers. Not `orderNumbers`
    # by name: the release moved the order number to the question of last resort
    # -- "an associate calling about a customer has the customer in front of
    # them and rarely the paperwork" -- so the top anchor is now the email, and
    # a test naming the field was asserting the priority table rather than the
    # zero-result rule it is about.
    assert ranked[0].intent_key == "emails"


def test_a_signal_already_given_is_not_asked_for_again(
    catalogue: IdentificationCatalogue,
) -> None:
    """The cheapest half of "do not re-ask"."""
    ranked = rank_discriminators(catalogue, _parsed(catalogue, orderNumbers=["10001"]))

    assert "orderNumbers" not in {item.intent_key for item in ranked}


def test_a_signal_whose_value_was_invalid_is_asked_for_again(
    catalogue: IdentificationCatalogue,
) -> None:
    """Answered and answered *usably* are different things.

    A value the configured validation rejected cannot be searched, so the
    question is still open -- and the ranking says that is why it came back.
    """
    discovery = load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.discovery
    payload = discovery.model_dump(mode="json")
    for entry in payload["identification_fields"]:
        if entry["field_id"] == "postal_code":
            entry["validation_pattern"] = "^[0-9]{5}$"
    from return_platform.configuration.return_configuration import DiscoveryConfiguration

    schema = load_active_schema(
        REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"
    )
    catalogue_with_validation = build_identification_catalogue(
        DiscoveryConfiguration.model_validate(payload).identification_fields,
        schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )
    parsed = _parsed(catalogue_with_validation, postalCodes=["not-a-zip"])
    ranked = rank_discriminators(catalogue_with_validation, parsed, limit=50)
    by_key = {item.intent_key: item for item in ranked}

    assert "postalCodes" in by_key
    assert "could not be validated" in by_key["postalCodes"].reason


def test_a_signal_nothing_can_search_is_never_suggested(
    catalogue: IdentificationCatalogue,
) -> None:
    """Colour is reported as unusable to the associate; it is not asked for.

    Suggesting a question whose answer no configured search can use would be a
    worse failure than dropping the signal silently -- the associate would be
    asked for something and then told it did not help.
    """
    ranked = rank_discriminators(catalogue, _parsed(catalogue), limit=50)

    assert "colors" not in {item.intent_key for item in ranked}


# --- ordering the searches themselves -----------------------------------------


def test_the_most_discriminating_search_runs_first(
    production_schema: ActiveSchema,
) -> None:
    """Only observable when the per-turn budget truncates -- which is the point.

    An associate who gives six signals could previously have the order-number
    pass dropped because it sat behind five broad address passes in catalogue
    order, and be told "no results" for a search that never ran the one query
    that would have answered it.
    """
    catalogue = _catalogue(
        _profiled(
            production_schema,
            {
                ("sales_order", "sales_order_number"): {
                    "sampled_rows": 50,
                    "null_rate": 0.0,
                    "approximate_distinct": 50,
                    "identifier_likelihood": "DECLARED_UNIQUE",
                }
            },
        )
    )
    program = build_search_program(
        OrderSearchIntent.model_validate(
            {
                "cities": ["Dallas"],
                "states": ["TX"],
                "postalCodes": ["75201"],
                "orderNumbers": ["10001"],
            }
        ),
        catalogue,
    )

    assert program.primary[0].intent_key == "orderNumbers"


def test_ordering_is_stable_for_equally_ranked_searches(
    catalogue: IdentificationCatalogue,
) -> None:
    """A turn has to be reproducible, so ties keep the catalogue's own order."""
    program = build_search_program(
        OrderSearchIntent.model_validate({"cities": ["Dallas"]}), catalogue
    )
    twice = build_search_program(
        OrderSearchIntent.model_validate({"cities": ["Dallas"]}), catalogue
    )

    assert [item.plan for item in program.primary] == [item.plan for item in twice.primary]


def test_ordering_never_drops_or_duplicates_a_search(
    catalogue: IdentificationCatalogue,
) -> None:
    """Reordering is a permutation. Losing a pass here would look like a miss."""
    planned = build_search_program(
        OrderSearchIntent.model_validate(
            {
                "cities": ["Dallas"],
                "states": ["TX"],
                "postalCodes": ["75201"],
                "orderNumbers": ["10001"],
                "emails": ["dana@example.com"],
            }
        ),
        catalogue,
    ).primary
    reordered = order_searches_by_discrimination(planned, catalogue)

    assert sorted(id(item) for item in reordered) == sorted(id(item) for item in planned)


def test_the_ranking_is_advice_and_carries_its_own_reasoning(
    catalogue: IdentificationCatalogue,
) -> None:
    """`field_selection_owner` is LLM and stays that way.

    Every entry states its basis and its reason, so the model is choosing on
    evidence it can weigh rather than obeying a number it cannot see behind.
    """
    described = [item.describe() for item in rank_discriminators(catalogue, _parsed(catalogue))]

    assert described
    for entry in described:
        assert entry["reason"]
        assert entry["basis"] in {basis.value for basis in DiscriminatorBasis}
        assert 0.0 <= entry["score"] <= 1.0
