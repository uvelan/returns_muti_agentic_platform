"""Turning feedback evidence into a typed improvement, or into nothing.

`FeedbackLearningService` has always produced recommendations. They were English
sentences -- "Move recurring support clarification fields into the associate
question plan" -- which is a thing a person can act on and a thing no system can.
W4.4 is about the other half: when the evidence supports a *specific* change to a
*permitted* key, say which key and to what value, so the change can be reviewed
as a diff and applied by a release rather than retyped by hand.

**Two rules, and no others.** Each is a direct reading of evidence the feedback
record already carries. Everything else the record knows -- graph sync ran, a bay
was used, a source was read -- maps to no permitted key, and inventing one would
produce a proposal whose justification is a guess wearing a schema.

**The step is a step, not a tuning.** Neither rule claims to know the right
value; each moves one documented increment in the direction the evidence points
and stops at the key's bound. The reviewer decides whether that is the change
they want, which is the entire reason this is a proposal.

**Nothing here activates anything** (plan section 7). This builds a document; the
kernel governs it, and a configuration release is what makes it real.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.platform.governance.key_policy import (
    PERMITTED_IMPROVEMENT_KEYS,
    PermittedKey,
)

__all__ = [
    "AMBIGUITY_GAP_STEP_MILLIONTHS",
    "SUPPORT_REWORK_EVENT_TYPES",
    "ImprovementChange",
    "build_improvement_changes",
    "changes_to_documents",
]

#: The workflow events that mean a human had to go back and ask again.
SUPPORT_REWORK_EVENT_TYPES: frozenset[str] = frozenset(
    {"SUPPORT_REVIEW_REQUIRED", "RETURN_SUPPORT_CLARIFICATION_REQUIRED"}
)

#: 2.5 percentage points, expressed in the millionths the field uses. One
#: documented step; deliberately not derived from the sample, because a single
#: return is not a distribution and a rule that pretended otherwise would move
#: the gap by an amount whose only justification is that it looked calculated.
AMBIGUITY_GAP_STEP_MILLIONTHS = 25_000

_PROMPTS_PER_TURN = "returns.discovery.clarification.max_prompts_per_turn"
_AMBIGUITY_GAP = "returns.discovery.scoring.ambiguity_gap_millionths"


@dataclass(frozen=True, slots=True)
class ImprovementChange:
    """One permitted key, its current value, and the value being proposed."""

    key: str
    before: int
    after: int
    reason: str

    @property
    def permitted(self) -> PermittedKey:
        return PERMITTED_IMPROVEMENT_KEYS[self.key]


def _stepped(key: str, current: int, delta: int) -> int | None:
    """Move one step, clamped to the key's server-side bounds.

    Returns None when the value is already at the bound: proposing a change of
    zero would put a row in the review queue that asks a person to approve
    nothing.
    """
    permitted = PERMITTED_IMPROVEMENT_KEYS[key]
    proposed = current + delta
    if permitted.minimum is not None:
        proposed = max(proposed, permitted.minimum)
    if permitted.maximum is not None:
        proposed = min(proposed, permitted.maximum)
    return None if proposed == current else proposed


def build_improvement_changes(
    *,
    configuration: ReturnPlatformConfiguration,
    event_types: Sequence[str],
    confirmed_order_line_count: int,
) -> tuple[ImprovementChange, ...]:
    """The changes this session's evidence supports. Often none, and that is the
    expected answer -- a proposal per return would make the queue unreadable."""
    changes: list[ImprovementChange] = []

    rework = sorted(SUPPORT_REWORK_EVENT_TYPES.intersection(event_types))
    if rework:
        current = configuration.clarification_policy.max_prompts_per_turn
        stepped = _stepped(_PROMPTS_PER_TURN, current, +1)
        if stepped is not None:
            changes.append(
                ImprovementChange(
                    key=_PROMPTS_PER_TURN,
                    before=current,
                    after=stepped,
                    reason=(
                        "the return needed human follow-up ("
                        + ", ".join(rework)
                        + "), so the associate turn is asking for less than it needs"
                    ),
                )
            )

    if confirmed_order_line_count != 1:
        # Discovery did not land on exactly one line. Raising the gap that counts
        # as unambiguous makes the agent ask rather than assume, which is the
        # direction the evidence points; how far is the reviewer's call.
        current = configuration.discovery.ambiguity_gap_millionths
        stepped = _stepped(_AMBIGUITY_GAP, current, AMBIGUITY_GAP_STEP_MILLIONTHS)
        if stepped is not None:
            changes.append(
                ImprovementChange(
                    key=_AMBIGUITY_GAP,
                    before=current,
                    after=stepped,
                    reason=(
                        f"the workflow confirmed {confirmed_order_line_count} order lines rather "
                        "than exactly one, so discovery treated an ambiguous match as decided"
                    ),
                )
            )

    return tuple(changes)


def changes_to_documents(
    changes: Sequence[ImprovementChange],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Nest the changes so their leaf paths *are* the plan's permitted keys.

    `diff_documents` addresses leaves by dotted path, so a document shaped this
    way makes `affected_keys` identical to the key names section 7 permits --
    which is what lets the kernel police them without a translation table that
    could disagree with the one the activator uses.
    """
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for change in changes:
        _assign(before, change.key, change.before)
        _assign(after, change.key, change.after)
    return before, after


def _assign(document: dict[str, Any], dotted_key: str, value: Any) -> None:
    segments = dotted_key.split(".")
    cursor = document
    for segment in segments[:-1]:
        nested = cursor.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            cursor[segment] = nested
        cursor = nested
    cursor[segments[-1]] = value


def reasons(changes: Sequence[ImprovementChange]) -> Mapping[str, str]:
    return {change.key: change.reason for change in changes}
