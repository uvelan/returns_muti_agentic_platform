"""The analyzer reasoning graph's checkpointed state (design doc section 14.4).

**No raw source samples, ever.** Samples are governed by the `NONE`/`REDACTED`/
`ENCRYPTED` classification (section 13.6) and live in the encrypted
`source_samples` structure; this state carries only `source_snapshot_id` and
`source_schema_hash`, and nodes fetch what they need at the moment they need it.
An analysis is long-lived and human-paced, so its checkpoints may sit at rest for
days — putting samples here would quietly create a second, unclassified copy with
a different retention story from the one the operator was promised.

`ANALYZER_CHECKPOINT_ALLOWLIST` and this TypedDict's keys must stay identical; a
test asserts it, so a field added here without adding it there fails loudly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

__all__ = [
    "ANALYZER_CHECKPOINT_ALLOWLIST",
    "AnalyzerState",
    "CompletionStatus",
    "NextAction",
]


class CompletionStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class NextAction(StrEnum):
    """What the last node decided should happen, consumed by routing.py.

    Explicit rather than inferred from state shape: a router that has to guess
    intent from which fields happen to be set becomes unreadable the moment a
    third branch appears.
    """

    ANALYZE = "ANALYZE"
    CLARIFY = "CLARIFY"
    PROPOSE = "PROPOSE"
    VALIDATE = "VALIDATE"
    REVISE = "REVISE"
    COMPLETE = "COMPLETE"
    ESCALATE = "ESCALATE"


class AnalyzerState(TypedDict, total=False):
    """One analysis run's reasoning state."""

    # Pinned identity, set at graph input and never mutated.
    analysis_id: str
    configuration_release_id: str
    source_snapshot_id: str
    source_schema_hash: str
    requirements: str

    # Accumulated work -- references and model-generated structure only.
    draft_id: str | None
    revision_id: str | None
    validation_result_id: str | None
    proposal: dict[str, Any] | None
    validation_findings: tuple[dict[str, Any], ...]
    clarification_exchanges: tuple[dict[str, str], ...]
    reasoning_notes: tuple[str, ...]

    # Budgets. Bounded loops, never "keep going until it works".
    clarification_count: int
    validation_attempt: int

    # Control.
    next_action: str
    completion_status: str
    escalation_reason: str | None


ANALYZER_CHECKPOINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "analysis_id",
        "configuration_release_id",
        "source_snapshot_id",
        "source_schema_hash",
        "requirements",
        "draft_id",
        "revision_id",
        "validation_result_id",
        "proposal",
        "validation_findings",
        "clarification_exchanges",
        "reasoning_notes",
        "clarification_count",
        "validation_attempt",
        "next_action",
        "completion_status",
        "escalation_reason",
    }
)
