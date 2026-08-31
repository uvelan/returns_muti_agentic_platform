"""Two seams either side of a question the agent asks and then waits on.

Both were found by an operator driving the Copilot in a browser, and neither was
covered by a test -- while the code on both sides of them was covered thoroughly.

* `rank_discriminators` has eleven tests, including one asserting that a field
  the candidates disagree on outranks one they share. Every one of them passes
  candidates in directly. **`_next_discriminators`, which finds those candidates,
  had none** -- so the ranker was correct and was never given anything to rank.

* `_extended_transcript` recorded the associate's message on a paused turn and
  not the agent's question, on the reasoning that `clarification_exchanges`
  carried it. That field is written from the value `interrupt()` *returns*, so
  while the question is pending it holds nothing -- and the transcript is what
  `read_conversation_transcript` serves to a human.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import _transcript_of
from return_platform.dynamic_knowledge.order_agent.coordinator import _extended_transcript
from return_platform.dynamic_knowledge.order_agent.graph_nodes import _next_discriminators
from return_platform.dynamic_knowledge.order_agent.identification import (
    build_identification_catalogue,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"

#: The five customers the operator's own run returned for "dane": distinct
#: customer ids on four different branch accounts, and nothing else populated at
#: the customer-resolution stage.
CANDIDATES = [
    {"candidate_id": "600654", "data": {"customer_id": "600654", "account_id": "NASH"}},
    {"candidate_id": "600318", "data": {"customer_id": "600318", "account_id": "GARDEN"}},
    {"candidate_id": "601159", "data": {"customer_id": "601159", "account_id": "DALLAS"}},
    {"candidate_id": "600399", "data": {"customer_id": "600399", "account_id": "GARDEN"}},
    {"candidate_id": "600175", "data": {"customer_id": "600175", "account_id": "LAKEWOOD"}},
]

FULL_ID = "0740e3bc-d6da-4204-9df6-a981f4cb00aa"
PAGE_ID = "b537115a-b093-4925-a94f-e8ef536e29a9"


class _Deps:
    """The one dependency `_next_discriminators` reaches for."""

    def __init__(self, identification: Any) -> None:
        self.identification = identification


@pytest.fixture(scope="module")
def deps() -> _Deps:
    schema = load_active_schema(CONFIG / "dynamic_knowledge" / "active-schema.return-order.yaml")
    discovery = load_return_configuration(
        CONFIG / "returns" / "production.yaml"
    ).configuration.discovery
    return _Deps(
        build_identification_catalogue(
            discovery.identification_fields,
            schema,
            default_fulltext_index=discovery.progressive.customer_fulltext_index,
        )
    )


def _evidence(query_execution_id: str, candidates: list[dict[str, Any]]) -> QueryEvidence:
    return QueryEvidence.create(
        query_execution_id=query_execution_id,
        schema_version="2026.08.04",
        graph_generation_id="gen-1",
        logical_plan_checksum="logical",
        compiled_query_checksum="compiled",
        result={"candidates": candidates, "total_found": 7},
    )


def _cache(**overrides: Any) -> dict[str, Any]:
    cache: dict[str, Any] = {
        "intent": {"customerNames": ["dane"], "searchMode": "DISCOVER", "confidence": 0.6},
        "evidenceRef": FULL_ID,
        "pageEvidenceRef": PAGE_ID,
        "shown": 5,
        "totalFound": 7,
    }
    cache.update(overrides)
    return cache


#: An order-level result, whose rows carry fields the catalogue can search.
#: Four share a city and one does not, so `cities` splits them 2 ways while
#: `states` splits them not at all.
ORDER_CANDIDATES = [
    {"candidate_id": "A1", "data": {"ship_to_city": "RENO", "ship_to_state": "NV"}},
    {"candidate_id": "A2", "data": {"ship_to_city": "RENO", "ship_to_state": "NV"}},
    {"candidate_id": "A3", "data": {"ship_to_city": "RENO", "ship_to_state": "NV"}},
    {"candidate_id": "A4", "data": {"ship_to_city": "DALLAS", "ship_to_state": "NV"}},
]


def _entry(ranked: tuple[dict[str, Any], ...], key: str) -> dict[str, Any] | None:
    return next((item for item in ranked if item.get("intentKey") == key), None)


def test_the_candidates_reach_the_ranker(deps: _Deps) -> None:
    """The defect, stated as the thing that was not true.

    One search writes two evidence records -- the full set, which the candidate
    set binds to, and the page, whose id is the only one appended to
    `evidence_refs`. Looking up the full id here matched nothing, so `candidates`
    was empty, `narrowing` was false, and no ranking ever said anything about the
    candidates in front of the associate.

    The per-candidate clause is the proof: it is only ever appended while
    narrowing, so its presence means the rows were found.
    """
    ranked = _next_discriminators(
        deps,  # type: ignore[arg-type]
        _cache(),
        [_evidence(PAGE_ID, CANDIDATES)],
    )

    assert ranked, "no discriminators were produced at all"
    assert any("candidate" in str(item.get("reason", "")) for item in ranked), (
        "no entry mentions the candidates, which is what happens when the rows "
        "were never found and the ranking silently fell back to configured order"
    )


def test_a_field_the_candidates_disagree_on_outranks_one_they_share(deps: _Deps) -> None:
    """Measurement beats the configured question order, end to end.

    `rank_discriminators` has always done this correctly when handed candidates;
    what was missing was anything handing them over. Asserted here through
    `_next_discriminators` rather than against the ranker directly, because the
    lookup between them is the part that had no test.
    """
    ranked = _next_discriminators(
        deps,  # type: ignore[arg-type]
        _cache(),
        [_evidence(PAGE_ID, ORDER_CANDIDATES)],
    )

    cities = _entry(ranked, "cities")
    assert cities is not None, [item.get("intentKey") for item in ranked]
    assert cities["distinctValuesAmongCandidates"] == 2
    assert "splits the 4 candidates into 2" in cities["reason"]

    states = _entry(ranked, "states")
    if states is not None:
        assert states["score"] == 0.0
        assert "every remaining candidate has the same value" in states["reason"]

    keys = [item.get("intentKey") for item in ranked]
    # A measured split beats an unprofiled configured field. Compared against
    # whichever field that is rather than against `orderNumbers`, which the
    # release has since demoted to the question of last resort and which no
    # longer reaches this list at all -- the assertion was reading a ranking
    # rule off one row of the priority table.
    unprofiled = [
        item.get("intentKey")
        for item in ranked
        if item.get("basis") == "CONFIGURED_PRIORITY" and item.get("intentKey") != "cities"
    ]
    assert unprofiled, f"nothing left to outrank: {keys}"
    assert all(keys.index("cities") < keys.index(other) for other in unprofiled), (
        f"the configured questions still outrank the field that splits them: {keys}"
    )


def test_a_cache_written_before_the_page_pointer_existed_still_resolves(deps: _Deps) -> None:
    """A conversation already in flight when this shipped keeps working.

    Its cache names only the full record, so that is what the fallback looks for.
    """
    ranked = _next_discriminators(
        deps,  # type: ignore[arg-type]
        _cache(pageEvidenceRef=None),
        [_evidence(FULL_ID, ORDER_CANDIDATES)],
    )

    cities = _entry(ranked, "cities")
    assert cities is not None
    assert cities["distinctValuesAmongCandidates"] == 2


def test_nothing_is_ranked_before_a_search_has_run(deps: _Deps) -> None:
    """The behaviour the docstring promises, kept: a ranking derived from no
    evidence would dress the configured question order up as a measurement."""
    assert _next_discriminators(deps, None, []) == ()  # type: ignore[arg-type]
    assert _next_discriminators(deps, {"evidenceRef": FULL_ID}, []) == ()  # type: ignore[arg-type]


# --- the transcript -----------------------------------------------------------


def _question() -> StructuredAgentResponse:
    return StructuredAgentResponse(
        status="AWAITING_INPUT",
        business_capability="entity-resolution",
        statements=[
            ResponseStatement(
                statement_id="q1",
                statement_type=StatementType.CLARIFICATION_QUESTION,
                text="Which branch is this Dane on?",
            )
        ],
        requested_input="The branch or account this customer is on.",
    )


def test_a_paused_turn_records_the_question_it_is_waiting_on() -> None:
    """Reopening the conversation has to show what the agent asked.

    `clarification_exchanges` does not cover this: it is written from the value
    `interrupt()` returns, so while the question is pending it is empty -- and
    the transcript is what a human is served.
    """
    transcript = _extended_transcript(
        {},
        user_message="find order for dane and the product he received is damaged",
        response=_question(),
    )

    assert [entry["role"] for entry in transcript] == ["associate", "agent"]
    assert "Which branch is this Dane on?" in transcript[1]["text"]


def test_a_turn_with_nothing_to_say_adds_no_agent_line() -> None:
    """A response carrying no statement text is not an empty agent message."""
    transcript = _extended_transcript({}, user_message="hello", response=None)

    assert [entry["role"] for entry in transcript] == ["associate"]


# --- reading a conversation that was written before any of this ---------------


def _turn(version: int, *texts: str) -> dict[str, Any]:
    return {
        "result": {
            "conversation_version": version,
            "response": {"statements": [{"text": text} for text in texts]},
        }
    }


def test_a_question_stored_before_the_fix_is_still_recoverable() -> None:
    """Fixing the writer does nothing for the conversations already in the store.

    Nothing was lost, though -- only mis-read. `turns` carries the whole result
    per turn, and on a paused turn the response *is* the question. This is the
    shape of a real conversation from before the fix: one associate message, one
    paused turn, and a stored transcript that never recorded the reply.
    """
    document = {
        "state": {"transcript": [{"role": "associate", "text": "find order for BOYLE"}]},
        "turns": {"k1": _turn(1, "Which order are you looking for?")},
    }

    assert _transcript_of(document) == (
        {"role": "associate", "text": "find order for BOYLE"},
        {"role": "agent", "text": "Which order are you looking for?"},
    )


def test_a_reply_already_recorded_is_not_repeated() -> None:
    """A completed turn's reply is in both records and must appear once."""
    document = {
        "state": {
            "transcript": [
                {"role": "associate", "text": "CQ800002"},
                {"role": "agent", "text": "One match on the GARDEN account."},
                {"role": "associate", "text": "Yes, that is the order."},
            ]
        },
        "turns": {
            "k1": _turn(1, "One match on the GARDEN account."),
            "k2": _turn(2, "What is coming back, and why?"),
        },
    }

    assert _transcript_of(document) == (
        {"role": "associate", "text": "CQ800002"},
        {"role": "agent", "text": "One match on the GARDEN account."},
        {"role": "associate", "text": "Yes, that is the order."},
        {"role": "agent", "text": "What is coming back, and why?"},
    )


def test_turns_are_read_in_conversation_order_not_insertion_order() -> None:
    """`turns` is keyed by idempotency key, so the ordering has to come from the
    version each result carries."""
    document = {
        "state": {
            "transcript": [
                {"role": "associate", "text": "first"},
                {"role": "associate", "text": "second"},
            ]
        },
        "turns": {"zzz": _turn(2, "reply two"), "aaa": _turn(1, "reply one")},
    }

    assert [entry["text"] for entry in _transcript_of(document)] == [
        "first",
        "reply one",
        "second",
        "reply two",
    ]


def test_a_transcript_and_a_turn_log_that_disagree_are_served_as_stored() -> None:
    """A transcript truncated to its limit while `turns` kept every turn cannot be
    zipped by position. Serving it unchanged is worse than a repair and much
    better than a plausible-looking wrong order."""
    document = {
        "state": {"transcript": [{"role": "associate", "text": "only one left"}]},
        "turns": {"k1": _turn(1, "a"), "k2": _turn(2, "b"), "k3": _turn(3, "c")},
    }

    assert _transcript_of(document) == ({"role": "associate", "text": "only one left"},)
