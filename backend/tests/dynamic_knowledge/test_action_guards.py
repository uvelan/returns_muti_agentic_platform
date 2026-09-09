"""Two rules that lived in the prompt and held only on the model they were
tested on, moved into the validator where they hold on every model.

Both were observed slipping on the lightweight tier on 2026-09-09: a customer
confirmation followed straight by CONFIRM_ORDER on the one order found, and
"black ABS DWV vent ell" searched as a product phrase that matches no
description because the colour lives on the catalogue entry."""

from __future__ import annotations

from typing import Any

from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    OrderConfirmation,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    misplaced_signal,
    unagreed_confirmation,
)
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    IdentificationField,
)


def _confirm(reference: str = "CO363355") -> AgentAction:
    return AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.CONFIRM_ORDER,
        decision_summary="confirming",
        order_confirmation=OrderConfirmation(
            candidate_set_id="set-1",
            candidate_id=reference,
            order_reference=reference,
            order_line_references=["1"],
        ),
    )


def _state(message: str, last_agent: str | None = None) -> dict[str, Any]:
    transcript: list[dict[str, str]] = [{"role": "associate", "text": "earlier"}]
    if last_agent is not None:
        transcript.append({"role": "agent", "text": last_agent})
    return {"user_message": message, "transcript": tuple(transcript)}


def test_a_message_naming_the_order_is_agreement() -> None:
    assert (
        unagreed_confirmation(
            _state("Confirm order CO363355, line 1: ECON 7-DAY PROG TSTAT."), _confirm()
        )
        is None
    )


def test_yes_to_an_order_the_agent_just_showed_is_agreement() -> None:
    state = _state(
        "Yes, that is the order. Please start the return.",
        last_agent="I found order CO363355 on CHARLOTTE. Is this the correct order?",
    )
    assert unagreed_confirmation(state, _confirm()) is None


def test_confirming_the_customer_is_not_agreeing_to_an_order() -> None:
    state = _state(
        "Confirm the customer WESTFIELD PLUMBING CO on account CHARLOTTE.",
        last_agent="I found Westfield Plumbing at the Charlotte branch. Is this the correct customer?",
    )
    error = unagreed_confirmation(state, _confirm())
    assert error is not None
    assert "CO363355" in error
    assert "CLARIFY" in error


def test_yes_to_a_question_that_did_not_show_the_order_is_not_agreement() -> None:
    state = _state("yes", last_agent="Which branch is this for: Nash or Charlotte?")
    assert unagreed_confirmation(state, _confirm()) is not None


def test_other_actions_are_untouched_by_the_confirmation_guard() -> None:
    action = AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="searching",
        search_intent=OrderSearchIntent(),
    )
    assert unagreed_confirmation(_state("anything"), action) is None


def _catalogue() -> IdentificationCatalogue:
    def field(field_id: str, intent_key: str, **overrides: Any) -> IdentificationField:
        values = {
            "field_id": field_id,
            "intent_key": intent_key,
            "label": field_id.replace("_", " "),
            "description": "",
            "aliases": (),
            "value_type": "STRING",
            "multiple": True,
            "normalization": "NONE",
            "validation": None,
            "sensitivity": "NONE",
            "ranking_weight": 0.1,
            "exact_match_bonus": 0.0,
            "clarification_priority": 50,
            "searches": (),
        }
        values.update(overrides)
        return IdentificationField(**values)

    return IdentificationCatalogue(
        fields=(
            field("product_description", "productNames"),
            field(
                "product_colour",
                "colors",
                searches_only_with="productNames",
                known_values=("white", "black", "matte black", "brass"),
            ),
        ),
        unresolved=(),
    )


def _search(**signals: Any) -> AgentAction:
    return AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="searching",
        search_intent=OrderSearchIntent(**signals),
    )


def test_a_colour_leading_the_product_phrase_is_lifted_into_its_own_signal() -> None:
    error = misplaced_signal(_catalogue(), _search(productNames=["black ABS DWV vent ell"]))
    assert error is not None
    assert "colors: ['black']" in error
    assert "productNames: ['ABS DWV vent ell']" in error


def test_the_longest_known_value_wins() -> None:
    error = misplaced_signal(_catalogue(), _search(productNames=["matte black lav faucet"]))
    assert error is not None
    assert "colors: ['matte black']" in error
    assert "productNames: ['lav faucet']" in error


def test_a_colour_at_the_end_and_in_free_text_is_caught_too() -> None:
    assert misplaced_signal(_catalogue(), _search(freeTextTerms=["shower arm brass"])) is not None


def test_a_search_that_already_carries_the_colour_signal_passes() -> None:
    assert (
        misplaced_signal(_catalogue(), _search(productNames=["ABS DWV vent ell"], colors=["black"]))
        is None
    )


def test_a_product_phrase_with_no_known_value_passes() -> None:
    assert misplaced_signal(_catalogue(), _search(productNames=["STRT LGTH PEX-B POT"])) is None


def test_a_value_that_is_only_the_colour_is_left_for_the_companion_rule() -> None:
    # "black" alone under productNames has no product words to move; the
    # companion rule (searches_only_with) is the one that asks for the product.
    assert misplaced_signal(_catalogue(), _search(productNames=["black"])) is None


def test_confirm_the_customer_after_the_agent_named_an_order_is_still_not_agreement() -> None:
    # The word "confirm" is in the message and the agent's last reply named the
    # order, which is the exact combination that created a case on 2026-09-09.
    state = _state(
        "Confirm the customer WESTFIELD PLUMBING CO on account CHARLOTTE.",
        last_agent="I found order CO363355 for Westfield Plumbing. Is order CO363355 the correct order?",
    )
    assert unagreed_confirmation(state, _confirm()) is not None


def test_a_search_with_no_signal_is_rejected_with_the_facts_it_reported() -> None:
    from return_platform.dynamic_knowledge.order_agent.contracts import ObservedFact
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import empty_search

    action = AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="searching for the black vent ell",
        search_intent=OrderSearchIntent(searchMode="SEARCH", confidence=0.8),
        observed_facts=[
            ObservedFact(fact="product_description", value="black ABS DWV vent ell"),
            ObservedFact(fact="product_colour", value="black"),
        ],
    )
    error = empty_search(_catalogue(), action)
    assert error is not None
    assert "no identifying signal" in error
    assert "product_description='black ABS DWV vent ell'" in error
    assert "productNames" in error


def test_a_search_with_a_signal_or_a_page_request_passes_the_empty_guard() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import empty_search

    assert empty_search(_catalogue(), _search(productNames=["vent ell"])) is None
    assert empty_search(_catalogue(), _search(wantsMoreResults=True)) is None
