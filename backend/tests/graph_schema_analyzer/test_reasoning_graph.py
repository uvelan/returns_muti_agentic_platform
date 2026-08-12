"""The analyzer reasoning loop, driven end to end with fake ports.

No AI provider and no graph database: every collaborator is a resolved port, so
the whole loop -- including its bounded-retry and escalation behaviour -- is
exercisable in-process. That is the practical payoff of the module's port
discipline, not just an architectural nicety.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.ports.ai_port import (
    ProposedNode,
    SchemaProposal,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import BuildHandle
from return_platform.graph_schema_analyzer.reasoning.graph import build_analyzer_graph
from return_platform.graph_schema_analyzer.reasoning.limits import AnalyzerBudgets
from return_platform.graph_schema_analyzer.reasoning.nodes import AnalyzerDependencies
from return_platform.graph_schema_analyzer.reasoning.state import (
    ANALYZER_CHECKPOINT_ALLOWLIST,
    AnalyzerState,
    CompletionStatus,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

SNAPSHOT = SourceSchemaSnapshot.create(
    snapshot_id="snap-1",
    analysis_id="a1",
    datasets=(
        DatasetMetadata(
            source_id="mongo_main",
            dataset_name="orders",
            fields=(FieldMetadata(field_name="order_id", declared_type="string"),),
        ),
    ),
    sample_classification=SampleClassification.NONE,
    captured_at=NOW,
)


class FakePersistence:
    async def load_snapshot(self, snapshot_id: str) -> SourceSchemaSnapshot:
        assert snapshot_id == "snap-1"
        return SNAPSHOT


class FakeReasoning:
    """Returns a scripted sequence of proposals, one per call."""

    def __init__(self, proposals: Sequence[SchemaProposal]) -> None:
        self._proposals = list(proposals)
        self.calls = 0
        self.last_blocks: list[Mapping[str, Any]] = []

    async def propose_schema(
        self,
        *,
        analysis_id: str,
        snapshot_content_hash: str,
        prompt_blocks: Sequence[Mapping[str, Any]],
    ) -> SchemaProposal:
        self.last_blocks = list(prompt_blocks)
        proposal = self._proposals[min(self.calls, len(self._proposals) - 1)]
        self.calls += 1
        return proposal


class FakeGraphTarget:
    """Reports findings for the first `failures` validations, then passes."""

    def __init__(self, failures: int = 0) -> None:
        self._failures = failures
        self.calls = 0

    async def compile_schema(self, *, draft: Mapping[str, Any]) -> Sequence[str]:
        return ()

    async def validate_schema(self, *, draft: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        self.calls += 1
        if self.calls <= self._failures:
            return ({"message": f"finding {self.calls}"},)
        return ()

    async def request_build(self, *, schema_id: str, activate: bool) -> BuildHandle:
        raise AssertionError("the reasoning graph must never trigger a build")

    async def publish_release(
        self, *, draft: Mapping[str, object], draft_id: str, approver: str, activate: bool
    ) -> object:
        raise AssertionError("this target must never publish")


def _proposal(*, open_questions: tuple[str, ...] = ()) -> SchemaProposal:
    return SchemaProposal(
        snapshot_content_hash=SNAPSHOT.content_hash,
        nodes=(
            ProposedNode(
                label="Order", properties=(), source_dataset="orders", rationale="obvious"
            ),
        ),
        relationships=(),
        open_questions=open_questions,
    )


def _deps(
    *,
    proposals: Sequence[SchemaProposal],
    failures: int = 0,
    budgets: AnalyzerBudgets | None = None,
) -> tuple[AnalyzerDependencies, FakeReasoning, FakeGraphTarget]:
    reasoning = FakeReasoning(proposals)
    target = FakeGraphTarget(failures)
    deps = AnalyzerDependencies(
        persistence=FakePersistence(),  # type: ignore[arg-type]
        reasoning=reasoning,
        graph_target=target,
        budgets=budgets or AnalyzerBudgets(),
    )
    return deps, reasoning, target


def _initial() -> AnalyzerState:
    return {
        "analysis_id": "a1",
        "configuration_release_id": "release-1",
        "source_snapshot_id": "snap-1",
        "requirements": "Model orders.",
        "clarification_count": 0,
        "validation_attempt": 0,
    }


# --- state hygiene ----------------------------------------------------------


def test_allowlist_and_state_keys_never_drift() -> None:
    assert set(AnalyzerState.__annotations__) == ANALYZER_CHECKPOINT_ALLOWLIST


def test_no_state_field_can_hold_a_raw_source_sample() -> None:
    """Section 14.4: samples stay under their classification, never in a
    checkpoint that may sit at rest for days with a different retention story."""
    forbidden = {"sample", "samples", "rows", "sample_rows", "records"}
    assert not (set(AnalyzerState.__annotations__) & forbidden)


# --- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_proposal_reaches_ready_for_approval() -> None:
    deps, reasoning, target = _deps(proposals=[_proposal()])
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    final = await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t1"}})

    assert final["completion_status"] == CompletionStatus.READY_FOR_APPROVAL
    assert reasoning.calls == 1
    assert target.calls == 1
    # Bound to the exact source shape it reasoned over.
    assert final["source_schema_hash"] == SNAPSHOT.content_hash


@pytest.mark.asyncio
async def test_the_model_only_ever_sees_six_framed_blocks() -> None:
    deps, reasoning, _ = _deps(proposals=[_proposal()])
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t2"}})

    assert [b["index"] for b in reasoning.last_blocks] == [1, 2, 3, 4, 5, 6]
    untrusted = [b for b in reasoning.last_blocks if not b["trusted"]]
    assert [b["kind"] for b in untrusted] == ["UNTRUSTED_SOURCE_SAMPLE"]


# --- clarification ----------------------------------------------------------


@pytest.mark.asyncio
async def test_open_questions_suspend_the_graph_and_resume_with_the_answer() -> None:
    deps, _reasoning, _ = _deps(
        proposals=[
            _proposal(open_questions=("Which field joins orders to customers?",)),
            _proposal(),
        ]
    )
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t3"}}

    paused = await graph.ainvoke(_initial(), config=config)
    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    payload = interrupts[0].value
    assert payload["question"] == "Which field joins orders to customers?"
    assert payload["analysis_id"] == "a1"

    resumed = await graph.ainvoke(Command(resume="customer_id"), config=config)
    assert resumed["completion_status"] == CompletionStatus.READY_FOR_APPROVAL
    assert resumed["clarification_count"] == 1
    assert resumed["clarification_exchanges"][0]["answer"] == "customer_id"


@pytest.mark.asyncio
async def test_the_interrupt_payload_carries_no_source_content() -> None:
    """Section 14.4: references and the question only."""
    deps, _, _ = _deps(proposals=[_proposal(open_questions=("Which key?",))])
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    paused = await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t4"}})
    payload = paused["__interrupt__"][0].value
    assert set(payload) == {"analysis_id", "draft_id", "revision_id", "question"}


@pytest.mark.asyncio
async def test_endless_clarification_escalates_instead_of_looping() -> None:
    """A model that keeps asking must terminate in a human decision, not spin."""
    deps, _reasoning, _ = _deps(
        proposals=[_proposal(open_questions=("Again?",))],
        budgets=AnalyzerBudgets(max_clarifications=2),
    )
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t5"}}

    state = await graph.ainvoke(_initial(), config=config)
    for _ in range(5):
        if "__interrupt__" not in state:
            break
        state = await graph.ainvoke(Command(resume="an answer"), config=config)

    assert state["completion_status"] == CompletionStatus.NEEDS_HUMAN_REVIEW
    assert state["clarification_count"] == 2
    assert "clarifying question" in state["escalation_reason"]


# --- validation loop --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_validation_is_revised_and_retried() -> None:
    deps, reasoning, target = _deps(proposals=[_proposal()], failures=1)
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    final = await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t6"}})

    assert final["completion_status"] == CompletionStatus.READY_FOR_APPROVAL
    assert target.calls == 2
    assert reasoning.calls == 2
    assert any("validation failures" in note for note in final["reasoning_notes"])


@pytest.mark.asyncio
async def test_repeated_validation_failure_escalates_within_budget() -> None:
    deps, _, target = _deps(
        proposals=[_proposal()], failures=99, budgets=AnalyzerBudgets(max_validation_attempts=2)
    )
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    final = await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t7"}})

    assert final["completion_status"] == CompletionStatus.NEEDS_HUMAN_REVIEW
    assert final["validation_attempt"] == 2
    assert target.calls == 2
    assert final["validation_findings"]


@pytest.mark.asyncio
async def test_a_revision_cannot_reset_the_budget() -> None:
    """Otherwise the bounded loop is not actually bounded."""
    deps, _, _ = _deps(
        proposals=[_proposal()], failures=99, budgets=AnalyzerBudgets(max_validation_attempts=3)
    )
    graph = build_analyzer_graph(deps, checkpointer=InMemorySaver())
    final = await graph.ainvoke(_initial(), config={"configurable": {"thread_id": "t8"}})
    assert final["validation_attempt"] == 3


# --- the lifecycle boundary -------------------------------------------------


def test_reasoning_never_names_a_graph_lifecycle_operation() -> None:
    """Reasoning stops at READY_FOR_APPROVAL (14.4). The realistic mistake is
    someone adding a convenient build/activate call here instead of routing
    through ApprovalService, so the whole package is scanned for those names."""
    reasoning_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "graph_schema_analyzer"
        / "reasoning"
    )
    forbidden = {"request_build", "activate", "drain", "retire", "cas", "execute_ddl"}
    offenders: list[tuple[str, str]] = []
    for path in sorted(reasoning_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offenders.append((path.name, node.attr))
            elif isinstance(node, ast.Name) and node.id in forbidden:
                offenders.append((path.name, node.id))
    assert not offenders, f"reasoning must not perform lifecycle operations; found: {offenders}"
