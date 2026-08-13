"""The agent's two blind spots: no memory, and no way to reach the source.

Both were whole features that existed in code and could never run. On-demand
sync is fully built -- coordinator, connectors, writer, node, anchors -- and the
system prompt never mentioned it, so the model was never told the escalation
exists and never once emitted the action. Conversation memory was worse: the
agent saw the current message and the previous search's cache, and nothing that
was actually said, so it could re-ask on turn three a question answered on turn
one.

Neither gap is visible from a unit test of the node, which is why neither was
caught. These assert the two contracts that make them reachable: the model can
see the anchors, and the model can see the conversation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext
from return_platform.dynamic_knowledge.order_agent.coordinator import (
    _extended_transcript,
    _stored_transcript,
)
from return_platform.dynamic_knowledge.order_agent.state import (
    ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST,
    TRANSCRIPT_LIMIT,
    OrderAgentGraphState,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(
        BACKEND_ROOT / "config/dynamic_knowledge/active-schema.return-order.yaml"
    )


@pytest.fixture(scope="module")
def order_agent_prompt() -> str:
    document = yaml.safe_load((BACKEND_ROOT / "config/ai_gateway.yaml").read_text(encoding="utf-8"))
    prompt: str = document["tasks"]["ORDER_AGENT_REASONING_V1"]["systemPrompt"]
    return prompt


# --- on-demand sync is reachable ---------------------------------------------


def test_the_model_can_see_the_anchors_it_must_name(production_schema: ActiveSchema) -> None:
    """A REQUEST_ON_DEMAND_SYNC needs a `strong_anchor_id` and that anchor's
    exact fields. The compact schema carried entities, fields and relationships
    and no anchors at all, so the action was unemittable however well the prompt
    described it."""
    gateway = Neo4jKnowledgeGateway(None, database="neo4j")  # type: ignore[arg-type]
    compact = asyncio.run(gateway.compact_schema(production_schema, "order-discovery-agent"))

    anchors = compact["strongAnchors"]
    assert anchors, "no anchors exposed: the escalation cannot be emitted"
    for anchor_id, anchor in anchors.items():
        assert anchor["entity"] in compact["entities"], anchor_id
        assert anchor["fields"], anchor_id
        for field in anchor["fields"]:
            assert field["field"] in compact["entities"][anchor["entity"]]["fields"], anchor_id
            assert field["operators"], anchor_id


def test_only_sync_capable_anchors_are_offered(production_schema: ActiveSchema) -> None:
    """An anchor that does not permit on-demand sync is not an option the model
    has, and offering it would produce a guard rejection the model cannot fix."""
    gateway = Neo4jKnowledgeGateway(None, database="neo4j")  # type: ignore[arg-type]
    compact = asyncio.run(gateway.compact_schema(production_schema, "order-discovery-agent"))
    for anchor_id in compact["strongAnchors"]:
        owner = next(
            entity
            for entity in production_schema.entities.values()
            if anchor_id in entity.strong_anchors
        )
        assert owner.strong_anchors[anchor_id].on_demand_sync_allowed, anchor_id


def test_the_prompt_describes_the_escalation_and_the_polite_ending(
    order_agent_prompt: str,
) -> None:
    """The action enum alone is not an instruction. Every piece the node
    requires has to be named, or the model emits a payload the guard rejects."""
    for required in (
        "REQUEST_ON_DEMAND_SYNC",
        "strong_anchor_request",
        "original_query_plan",
        "value_origin",
        "USER_MESSAGE",
        "contextJson.compact_schema.strongAnchors",
    ):
        assert required in order_agent_prompt, required
    # And the end of the ladder: say so, rather than trailing off.
    assert "could not find" in order_agent_prompt


# --- the agent remembers ------------------------------------------------------


def test_transcript_is_checkpointable() -> None:
    """`CheckpointRedactor.enforce()` fails closed on any state key absent from
    the allowlist, so a field added to the state alone would break every
    checkpoint write at runtime rather than here."""
    assert "transcript" in ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST
    assert set(OrderAgentGraphState.__annotations__) == ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST


def test_the_turn_context_carries_the_conversation() -> None:
    context = AgentTurnContext(
        conversation_id="c1",
        client_turn_id="t1",
        user_message="the chrome one",
        as_of=datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
        session_timezone="UTC",
        schema_version="v1",
        graph_generation_id="g1",
        configuration_release_id="r1",
        policy_version="p1",
        prompt_version="p1",
        compact_schema={},
        conversation_state={},
        transcript=({"role": "associate", "text": "a faucet"},),
    )
    assert context.model_dump(mode="json")["transcript"] == [
        {"role": "associate", "text": "a faucet"}
    ]


def test_a_turn_appends_both_halves_of_the_exchange() -> None:
    class _Statement:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Response:
        statements = (_Statement("Order CW273354."), _Statement("Start the return?"))

    extended = _extended_transcript(
        {"transcript": [{"role": "associate", "text": "melgon"}]},
        user_message="the draft motor",
        response=_Response(),  # type: ignore[arg-type]
    )
    assert [entry["role"] for entry in extended] == ["associate", "associate", "agent"]
    assert extended[-1]["text"] == "Order CW273354. Start the return?"


def test_a_paused_turn_still_records_what_the_associate_said() -> None:
    """A CLARIFY pause produces no response, but the associate's message is
    still part of the conversation -- dropping it would make the resumed turn
    look like the question came from nowhere."""
    extended = _extended_transcript({}, user_message="melgon heating", response=None)
    assert extended == [{"role": "associate", "text": "melgon heating"}]


def test_the_transcript_is_bounded() -> None:
    """It rides in every checkpoint write and every prompt. Unbounded, a long
    conversation eventually trips the gateway's input cap -- and long
    conversations are exactly the ones where the history matters."""
    state: dict[str, Any] = {
        "transcript": [{"role": "associate", "text": str(index)} for index in range(50)]
    }
    extended = _extended_transcript(state, user_message="latest", response=None)
    assert len(extended) == TRANSCRIPT_LIMIT
    assert extended[-1]["text"] == "latest"


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "not a list",
        [{"role": "associate"}],
        [{"text": "no role"}],
        ["a bare string"],
    ],
)
def test_a_malformed_stored_transcript_is_dropped_not_trusted(stored: Any) -> None:
    """The record is persisted JSON that a migration or a hand-edit could have
    left in any shape, and anything kept here reaches the reasoning prompt."""
    assert _stored_transcript({"transcript": stored}) == ()


def test_memory_is_scoped_to_one_session_and_does_not_cross() -> None:
    """A session is the unit of memory. Sessions are independent.

    The transcript lives on the conversation record and is seeded from it, so a
    different conversation id starts empty by construction -- there is no store
    that spans them and nothing to leak. Asserted rather than assumed, because
    the natural "improvement" here is a per-associate history, and that would
    silently carry one customer's details into the next customer's return.
    """
    first = _extended_transcript({}, user_message="melgon heating", response=None)
    assert first == [{"role": "associate", "text": "melgon heating"}]

    # A new session's record carries no transcript at all.
    assert _stored_transcript({}) == ()
    assert _extended_transcript({}, user_message="different customer", response=None) == [
        {"role": "associate", "text": "different customer"}
    ]
