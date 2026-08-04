from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from return_platform.dynamic_knowledge.order_agent.state import CandidateSet


def test_candidate_set_is_generation_pinned_and_immutable() -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    candidate_set = CandidateSet.create(
        candidate_set_id="cs1",
        conversation_id="c1",
        turn_id="t1",
        principal_id="p1",
        tenant_id="tenant1",
        schema_version="v1",
        graph_generation_id="g1",
        query_execution_id="q1",
        candidate_ids=("x", "y"),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    candidate_set.validate_selection(
        candidate_id="x",
        conversation_id="c1",
        principal_id="p1",
        tenant_id="tenant1",
        graph_generation_id="g1",
        now=now,
    )
    with pytest.raises(ValueError, match="stale graph generation"):
        candidate_set.validate_selection(
            candidate_id="x",
            conversation_id="c1",
            principal_id="p1",
            tenant_id="tenant1",
            graph_generation_id="g2",
            now=now,
        )
