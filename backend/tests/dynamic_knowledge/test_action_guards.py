"""Two rules that lived in the prompt and held only on the model they were
tested on, moved into the validator where they hold on every model.

Both were observed slipping on the lightweight tier on 2026-09-09: a customer
confirmation followed straight by CONFIRM_ORDER on the one order found, and
"black ABS DWV vent ell" searched as a product phrase that matches no
description because the colour lives on the catalogue entry."""

from __future__ import annotations

from typing import Any

import pytest

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


# --- orders_not_read / repeated_search -----------------------------------------
#
# The third rule to leave the prompt (2026-09-10): "Confirm the customer
# SILVERLAKE AIR SYSTEMS on account SACRAMENTO" was answered with three identical
# customer searches and "I couldn't find any orders", for a customer with an
# eight-line order that no query had ever gone from the customer to.


def _reply(action_type: ActionType = ActionType.RESPOND) -> AgentAction:
    from return_platform.dynamic_knowledge.knowledge.evidence import (
        ResponseStatement,
        StatementType,
        StructuredAgentResponse,
    )

    return AgentAction(
        business_capability="order-discovery",
        action_type=action_type,
        decision_summary="answering",
        response=StructuredAgentResponse(
            status="COMPLETE" if action_type is ActionType.RESPOND else "NEEDS_CLARIFICATION",
            business_capability="order-discovery",
            statements=(
                ResponseStatement(
                    statement_id="s1",
                    statement_type=StatementType.REASONED_SUGGESTION,
                    text="I couldn't find any orders matching that name.",
                    evidence_refs=(),
                ),
            ),
            requested_input=None if action_type is ActionType.RESPOND else "Which order?",
        ),
    )


def test_the_select_button_message_confirms_a_customer_and_names_the_account() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import confirmed_customer

    assert confirmed_customer(
        "Confirm the customer SILVERLAKE AIR SYSTEMS on account SACRAMENTO."
    ) == ("SILVERLAKE AIR SYSTEMS", "SACRAMENTO")
    assert confirmed_customer("confirm customer northgate plumbing") == (
        "northgate plumbing",
        None,
    )
    # An order named in the message makes it an order confirmation, whatever
    # else it mentions; that is `unagreed_confirmation`'s question.
    assert confirmed_customer("Confirm order CO363355 for customer Westfield") is None
    assert confirmed_customer("find orders for MORGAN") is None


def test_a_reply_to_a_customer_confirmation_before_any_graph_query_is_sent_back() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import orders_not_read

    state = _state("Confirm the customer SILVERLAKE AIR SYSTEMS on account SACRAMENTO.")
    for action in (_reply(ActionType.RESPOND), _reply(ActionType.CLARIFY)):
        error = orders_not_read(state, action)
        assert error is not None
        assert "customer_name CONTAINS 'SILVERLAKE AIR SYSTEMS'" in error
        assert "account_id EQUALS 'SACRAMENTO'" in error
        assert "TRAVERSE" in error


def test_the_reply_passes_once_the_turn_has_read_the_graph_or_a_case_exists() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import orders_not_read

    message = "Confirm the customer SILVERLAKE AIR SYSTEMS on account SACRAMENTO."
    assert orders_not_read({**_state(message), "graph_queries_used": 1}, _reply()) is None
    assert orders_not_read({**_state(message), "case_id": "case-1"}, _reply()) is None
    # A message that confirms nothing is not this rule's business.
    assert orders_not_read(_state("find orders for MORGAN"), _reply()) is None
    # Nor is a search: the guard is about answering, not looking.
    assert orders_not_read(_state(message), _search(customerNames=["SILVERLAKE"])) is None


def _cache(signature: str, *, turn_id: str) -> dict[str, Any]:
    return {
        "signature": signature,
        "shown": 5,
        "totalFound": 7,
        "candidateSet": {"candidate_set_id": "set-1", "turn_id": turn_id},
    }


def test_the_same_search_twice_on_one_turn_is_sent_back_with_what_it_found() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import repeated_search
    from return_platform.dynamic_knowledge.order_agent.search_strategy import (
        search_intent_signature,
    )

    catalogue = _catalogue()
    action = _search(customerNames=["SILVERLAKE AIR SYSTEMS"])
    assert action.search_intent is not None
    signature = search_intent_signature(action.search_intent, catalogue)
    state = {
        **_state("Confirm the customer SILVERLAKE AIR SYSTEMS on account SACRAMENTO."),
        "client_turn_id": "turn-2",
        "order_search_cache": _cache(signature, turn_id="turn-2"),
    }
    error = repeated_search(catalogue, state, action)
    assert error is not None
    assert "already ran on this turn" in error
    assert "found 7" in error


def test_a_page_request_a_new_search_or_an_earlier_turn_is_not_a_repeat() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import repeated_search
    from return_platform.dynamic_knowledge.order_agent.search_strategy import (
        search_intent_signature,
    )

    catalogue = _catalogue()
    action = _search(customerNames=["SILVERLAKE AIR SYSTEMS"])
    assert action.search_intent is not None
    signature = search_intent_signature(action.search_intent, catalogue)
    base = {**_state("anything"), "client_turn_id": "turn-2"}

    same_turn = {**base, "order_search_cache": _cache(signature, turn_id="turn-2")}
    assert repeated_search(catalogue, same_turn, _search(wantsMoreResults=True)) is None
    assert repeated_search(catalogue, same_turn, _search(customerNames=["MORGAN"])) is None

    earlier_turn = {**base, "order_search_cache": _cache(signature, turn_id="turn-1")}
    assert repeated_search(catalogue, earlier_turn, action) is None
    assert repeated_search(catalogue, {**base, "order_search_cache": None}, action) is None


# --- repeated_query -----------------------------------------------------------
#
# Twelve identical traverses on one turn (2026-09-10), nine rows in contextJson
# every time, until the query budget ended the turn with nothing said.


def _traverse(limit: int = 20) -> AgentAction:
    from return_platform.dynamic_knowledge.knowledge.query_plan import (
        LogicalQueryPlan,
        QueryCondition,
        QueryOperation,
        TraversalStep,
    )

    return AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.GRAPH_QUERY,
        decision_summary="reading the customer's orders",
        query_plan=LogicalQueryPlan(
            operation=QueryOperation.TRAVERSE,
            start_entity_id="customer",
            fields=("sales_order_number", "product_description"),
            filters=(
                QueryCondition(
                    entity_id="customer",
                    field_id="customer_name",
                    operator="CONTAINS",
                    value="SILVERLAKE AIR SYSTEMS",
                ),
            ),
            traversal=(
                TraversalStep(
                    relationship_id="customer_placed_order",
                    direction="OUTBOUND",
                    target_entity_id="sales_order",
                ),
            ),
            limit=limit,
        ),
    )


class _EvidenceOf:
    def __init__(self, *items: Any) -> None:
        self.items = {item.query_execution_id: item for item in items}

    async def get_many(self, query_execution_ids: Any) -> tuple[Any, ...]:
        return tuple(self.items[identifier] for identifier in query_execution_ids)


class _Deps:
    def __init__(self, evidence: _EvidenceOf) -> None:
        self.evidence_store = evidence


def _answered(action: AgentAction, rows: list[dict[str, Any]], *, execution_id: str) -> Any:
    from return_platform.dynamic_knowledge.fingerprint import sha256_digest
    from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence

    assert action.query_plan is not None
    return QueryEvidence.create(
        query_execution_id=execution_id,
        schema_version="2026.08.04",
        graph_generation_id="gen-1",
        logical_plan_checksum=sha256_digest(action.query_plan.model_dump(mode="json")),
        compiled_query_checksum="compiled",
        result={"rows": rows, "count": len(rows)},
    )


@pytest.mark.asyncio
async def test_the_same_query_plan_twice_on_one_turn_is_sent_back_to_its_own_rows() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import repeated_query

    action = _traverse()
    rows = [{"sales_order_number": "CL712980", "product_description": "1 X 36 GAS HOSE QD KIT"}]
    evidence = _answered(action, rows, execution_id="qe-traverse")
    state = {
        **_state("Confirm the customer SILVERLAKE AIR SYSTEMS."),
        "evidence_refs": ("qe-traverse",),
    }

    error = await repeated_query(_Deps(_EvidenceOf(evidence)), state, action)  # type: ignore[arg-type]

    assert error is not None
    assert "already ran on this turn" in error
    assert "qe-traverse" in error
    assert "1 row(s)" in error
    assert "product_description" in error


@pytest.mark.asyncio
async def test_a_different_plan_a_fresh_turn_or_a_search_is_not_a_repeated_query() -> None:
    from return_platform.dynamic_knowledge.order_agent.graph_nodes import repeated_query

    action = _traverse()
    evidence = _answered(action, [], execution_id="qe-traverse")
    deps = _Deps(_EvidenceOf(evidence))
    base = _state("anything")

    # A different limit is a different plan.
    assert (
        await repeated_query(deps, {**base, "evidence_refs": ("qe-traverse",)}, _traverse(50))
        is None
    )  # type: ignore[arg-type]
    # A turn with no evidence yet has nothing to repeat.
    assert await repeated_query(deps, {**base, "evidence_refs": ()}, action) is None  # type: ignore[arg-type]
    # Searches have their own rule.
    assert (
        await repeated_query(
            deps, {**base, "evidence_refs": ("qe-traverse",)}, _search(customerNames=["x"])
        )
        is None
    )  # type: ignore[arg-type]
