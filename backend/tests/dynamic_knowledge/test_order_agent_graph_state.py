"""Pure, fixture-free proof that OrderAgentGraphState and its checkpoint
allowlist agree, and that CheckpointRedactor genuinely enforces it -- every
key the LangGraph decomposition can write to a checkpoint must be declared
here, and nothing else is ever allowed through.
"""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.order_agent.state import (
    ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST,
    OrderAgentGraphState,
)
from return_platform.platform.reasoning.errors import CheckpointRedactionViolation
from return_platform.platform.reasoning.redaction import CheckpointRedactor


def test_allowlist_matches_the_graph_state_schema_exactly() -> None:
    assert set(OrderAgentGraphState.__annotations__) == ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST


def _full_state() -> OrderAgentGraphState:
    return OrderAgentGraphState(
        conversation_id="conv-1",
        client_turn_id="turn-1",
        user_message="Where is my order?",
        schema_version="2026.08.1",
        graph_generation_id="gen-1",
        configuration_release_id="release-1",
        policy_version="2026.08.1",
        prompt_version="2026.08.1",
        agent_id="agent_a",
        run_id="run-1",
        requested_schema_entity_ids=("entity_a",),
        evidence_refs=("evidence-1",),
        order_search_cache=None,
        action=None,
        last_provider="nvidia",
        last_model="model-1",
        capability_validated=True,
        reasoning_steps_used=1,
        queries_used=1,
        correction_attempts=0,
        clarifications_used=0,
        replans_used=0,
        targeted_syncs_used=0,
        final_response=None,
    )


def test_redactor_accepts_every_field_this_graph_produces() -> None:
    redactor = CheckpointRedactor(ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST)
    state = _full_state()
    assert redactor.enforce(state) == state


def test_redactor_rejects_a_key_outside_the_allowlist() -> None:
    redactor = CheckpointRedactor(ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST)
    state = {**_full_state(), "raw_query_evidence": {"result": "should never be here"}}
    with pytest.raises(CheckpointRedactionViolation, match="raw_query_evidence"):
        redactor.enforce(state)
