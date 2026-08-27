"""One customer, one candidate -- however many deferred searches found them.

The recovery pass built its candidate list with `extend`, so a customer arrived
once per matching row and once per deferred search that matched. Neither
multiplier is hypothetical: a contact has one `ContactPoint` per channel, so a
person with a phone and an email is two rows, and an ambiguous given name
populates both `customerNames` and `contactNames`, so two configured recoveries
look for it.

Observed against the seeded corpus: two people called ALEX were presented to the
associate as eight candidates -- the same trade name listed three times in the
candidate pane. `suggested_discriminators` then reported "splits the 5
candidates into 5" and ranked a question that splits nothing, because it was
counting duplicates as distinct customers.

`rank_search_results` has always merged the primary pass by candidate key. This
is the same rule on the path that runs when the primary pass found nothing.
"""

from __future__ import annotations

from typing import Any

from return_platform.dynamic_knowledge.knowledge.cypher_compiler import FULLTEXT_SCORE_FIELD
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.graph_nodes import _rank_deferred_matches
from return_platform.dynamic_knowledge.order_agent.identification import ResolvedSearch
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    CustomerFulltextPolicy,
    PlannedSearch,
)


def _planned(label: str, ceiling: float) -> PlannedSearch:
    return PlannedSearch(
        plan=LogicalQueryPlan(
            operation=QueryOperation.FULLTEXT_SEARCH,
            start_entity_id="contact_point",
            fields=("customer_id", "customer_name"),
            filters=(),
            limit=25,
            fulltext_index="contact_name_search_v1",
            fulltext_field_id="contact_first_name",
            fulltext_query="alex*",
        ),
        search=ResolvedSearch(
            entity_id="contact_point",
            field_id="contact_first_name",
            strategy="FULLTEXT",
            limit=25,
            result_fields=("customer_id", "customer_name"),
            value_form="AS_TYPED",
            applies_when=None,
            narrow_with=None,
            fulltext_index="contact_name_search_v1",
            only_when_nothing_found=True,
            match_label=label,
            deferred_score_ceiling=ceiling,
            distinct_ratio=None,
            identifier_likelihood="UNKNOWN",
        ),
        intent_key="contactNames",
    )


def _row(customer_id: str, score: float, **extra: Any) -> dict[str, Any]:
    return {"customer_id": customer_id, FULLTEXT_SCORE_FIELD: score, **extra}


def test_one_customer_found_on_two_contact_rows_is_one_candidate() -> None:
    """The phone row and the email row are the same customer."""
    planned = [_planned("contact_name_fuzzy", 0.55)]
    results = [{"rows": [_row("859928", 2.4), _row("859928", 2.4)]}]

    candidates = _rank_deferred_matches(planned, results, policy=CustomerFulltextPolicy())

    assert [candidate["candidate_id"] for candidate in candidates] == ["859928"]


def test_a_customer_two_recoveries_found_carries_both_labels() -> None:
    """The labels union rather than the second overwriting the first.

    The reasoning prompt reads `customer_name_fuzzy` to decide whether to hedge
    about spelling, so a label dropped in the merge is a hedge the agent never
    makes -- and the stronger of the two ceilings is the honest score, because a
    row two recoveries agree on is better evidence than one either found alone.
    """
    planned = [_planned("contact_name_fuzzy", 0.55), _planned("contact_name_fallback", 0.45)]
    results = [{"rows": [_row("859928", 2.4)]}, {"rows": [_row("859928", 2.4)]}]

    candidates = _rank_deferred_matches(planned, results, policy=CustomerFulltextPolicy())

    assert len(candidates) == 1
    assert sorted(candidates[0]["matches"]) == ["contact_name_fallback", "contact_name_fuzzy"]
    assert candidates[0]["score"] == 0.55


def test_two_customers_behind_one_given_name_stay_two_candidates() -> None:
    """Merging must not collapse the disambiguation the associate needs."""
    planned = [_planned("contact_name_fuzzy", 0.55)]
    results = [{"rows": [_row("859928", 2.4), _row("1051789", 2.4), _row("859928", 2.4)]}]

    candidates = _rank_deferred_matches(planned, results, policy=CustomerFulltextPolicy())

    assert sorted(candidate["candidate_id"] for candidate in candidates) == ["1051789", "859928"]


def test_a_narrower_second_plan_does_not_drop_what_the_first_returned() -> None:
    """Same rule `rank_search_results` states: merge, never overwrite."""
    planned = [_planned("contact_name_fuzzy", 0.55), _planned("contact_name_fallback", 0.45)]
    results = [
        {"rows": [_row("859928", 2.4, customer_name="MERIDIAN HEATING & COOLING")]},
        {"rows": [_row("859928", 2.4)]},
    ]

    candidates = _rank_deferred_matches(planned, results, policy=CustomerFulltextPolicy())

    assert candidates[0]["data"]["customer_name"] == "MERIDIAN HEATING & COOLING"
