"""Reopening a conversation gives back the table the agent had on screen.

The transcript carried what was *said* and nothing that was *shown*. So a past
search -- one that never reached a case, which is most of them -- came back as a
conversation with an empty results pane, and the associate had to run the search
again to see rows the agent had already found and quoted in the message above
them.

Nothing was lost. `turns` holds every turn's whole result, evidence and all --
the same record `_transcript_of` recovers the agent's replies from.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
    ConversationScope,
)

SCOPE = ConversationScope(tenant_id="acme", principal_id="associate-1")

CUSTOMER_PAGE = "6ef79ea9-a704-405f-8459-c2e262a38022"
LINE_PAGE = "ae45d58e-c4df-4f0b-a4e3-d336db1ff0cc"


def _evidence(query_execution_id: str, result: Any) -> dict[str, Any]:
    return {
        "query_execution_id": query_execution_id,
        "schema_version": "2026.08.04",
        "graph_generation_id": "gen-1",
        "logical_plan_checksum": "logical",
        "compiled_query_checksum": "compiled",
        "result": result,
        "result_checksum": "checksum",
    }


def _customers() -> dict[str, Any]:
    return _evidence(
        CUSTOMER_PAGE,
        {
            "total_found": 7,
            "candidates": [
                {"data": {"customer_id": "600654", "account_id": "NASH"}},
                {"data": {"customer_id": "600318", "account_id": "GARDEN"}},
            ],
        },
    )


def _lines() -> dict[str, Any]:
    return _evidence(
        LINE_PAGE,
        {"rows": [{"sales_order_number": "CG800991", "line_number": "4", "sku": "DEPJSGA"}]},
    )


def _turn(
    version: int,
    *,
    evidence: list[dict[str, Any]] | None = None,
    cites: str | None = None,
) -> dict[str, Any]:
    statement: dict[str, Any] = {
        "statement_id": "s1",
        "statement_type": "GRAPH_FACT" if cites else "CLARIFICATION_QUESTION",
        "text": "Here is what I found.",
    }
    if cites is not None:
        statement["evidence_refs"] = [
            {"query_execution_id": cites, "result_path": ["candidates", "0"]}
        ]
    return {
        "result": {
            "conversation_id": "disc-1",
            "conversation_version": version,
            "client_turn_id": f"ui-{version}",
            "graph_generation_id": "gen-1",
            "response": {
                "status": "AWAITING_INPUT",
                "business_capability": "order-discovery",
                "statements": [statement],
            },
            "query_evidence": evidence or [],
            "model_provider": "MANUAL",
            "model_name": "manual",
        }
    }


class _Store:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    async def read(self, conversation_id: str, *, scope: ConversationScope) -> dict[str, Any]:
        assert scope == SCOPE
        return self.document

    async def list_recent(self, *, scope: ConversationScope, limit: int = 30) -> list[Any]:
        raise AssertionError("not part of this behaviour")

    async def compare_and_set(self, **_: Any) -> bool:
        raise AssertionError("not part of this behaviour")


def _document(turns: dict[str, Any]) -> dict[str, Any]:
    return {
        "_id": "disc-1",
        "version": len(turns),
        "state": {"transcript": [{"role": "associate", "text": "find order for dane"}]},
        "turns": turns,
    }


async def _transcript(document: dict[str, Any]) -> Any:
    return await AtomicConversationRepository(_Store(document)).read_transcript(
        "disc-1", scope=SCOPE
    )


@pytest.mark.asyncio
async def test_the_results_come_back_with_the_conversation() -> None:
    """The defect, as the thing that was not true."""
    transcript = await _transcript(
        _document({"k1": _turn(1, evidence=[_customers()], cites=CUSTOMER_PAGE)})
    )

    assert transcript is not None
    turn = transcript.lastResultTurn
    assert turn is not None
    assert [record.query_execution_id for record in turn.query_evidence] == [CUSTOMER_PAGE]
    assert turn.query_evidence[0].result["total_found"] == 7


@pytest.mark.asyncio
async def test_the_whole_turn_travels_so_its_citations_can_be_read() -> None:
    """A turn runs more queries than it speaks about.

    Narrowing to an order takes a customer lookup first and an order lookup
    second, and both land in `query_evidence`. Which of them was on screen is
    decided by the citations the turn's own statements carry -- so the turn
    travels, and the rule that reads it is not written a second time here.
    """
    transcript = await _transcript(
        _document({"k1": _turn(1, evidence=[_customers(), _lines()], cites=LINE_PAGE)})
    )

    assert transcript is not None
    turn = transcript.lastResultTurn
    assert turn is not None
    assert {record.query_execution_id for record in turn.query_evidence} == {
        CUSTOMER_PAGE,
        LINE_PAGE,
    }
    cited = {
        reference.query_execution_id
        for statement in turn.response.statements
        for reference in (statement.evidence_refs or ())
    }
    assert cited == {LINE_PAGE}


@pytest.mark.asyncio
async def test_a_question_does_not_clear_the_table_it_is_asking_about() -> None:
    """The most recent turn *carrying results*, not simply the most recent turn.

    Asking a clarifying question leaves the matches on screen -- that is what
    the live screen does, since a turn with no results does not replace the
    table -- so the last turn to carry any is the one still up.
    """
    transcript = await _transcript(
        _document(
            {
                "k1": _turn(1, evidence=[_customers()], cites=CUSTOMER_PAGE),
                "k2": _turn(2),
            }
        )
    )

    assert transcript is not None
    turn = transcript.lastResultTurn
    assert turn is not None
    assert turn.conversation_version == 1


@pytest.mark.asyncio
async def test_the_latest_results_win_over_an_earlier_set() -> None:
    """`turns` is keyed by idempotency key, so the ordering has to come from the
    version each result carries rather than from insertion order."""
    transcript = await _transcript(
        _document(
            {
                "zzz": _turn(2, evidence=[_lines()], cites=LINE_PAGE),
                "aaa": _turn(1, evidence=[_customers()], cites=CUSTOMER_PAGE),
            }
        )
    )

    assert transcript is not None
    turn = transcript.lastResultTurn
    assert turn is not None
    assert turn.conversation_version == 2
    assert [record.query_execution_id for record in turn.query_evidence] == [LINE_PAGE]


@pytest.mark.asyncio
async def test_a_conversation_that_never_searched_carries_no_results() -> None:
    """Absence is an ordinary answer here, not a failure."""
    transcript = await _transcript(_document({"k1": _turn(1)}))

    assert transcript is not None
    assert transcript.lastResultTurn is None
    assert transcript.messages  # the words are still served


@pytest.mark.asyncio
async def test_a_turn_that_will_not_validate_does_not_fail_the_read() -> None:
    """A legacy turn is a reason to show an older table, never a reason to fail
    the read that serves the conversation itself."""
    broken = _turn(2, evidence=[_lines()], cites=LINE_PAGE)
    del broken["result"]["response"]

    transcript = await _transcript(
        _document({"k1": _turn(1, evidence=[_customers()], cites=CUSTOMER_PAGE), "k2": broken})
    )

    assert transcript is not None
    turn = transcript.lastResultTurn
    assert turn is not None
    assert turn.conversation_version == 1
    assert transcript.messages
