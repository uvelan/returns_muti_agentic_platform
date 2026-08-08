"""Typed reasoning outcomes.

Distinct from the receipt state machine's own states (receipts.py) -- these are the
exceptions that propagate out of the reasoning platform's own operations (redaction
violations, retention/abandonment invariant breaks), not the receipt lifecycle itself.
"""

from __future__ import annotations


class ReasoningError(RuntimeError):
    """Base for every typed error this package raises."""


class CheckpointRedactionViolation(ReasoningError):
    """Checkpoint state contained a key outside the component's declared allowlist.

    Raised, never silently stripped -- a violation must surface in development rather
    than becoming a quiet data-shape change (design doc "checkpoint content allowlist").
    """


class ReceiptFailedFinal(ReasoningError):
    """A reasoning action's receipt reached FAILED_FINAL; the typed outcome it recorded
    is raised to the caller rather than retried."""


class ReceiptUnresolvable(ReasoningError):
    """A receipt is STARTED (or PENDING_EXTERNAL) but its external_ref cannot be
    resolved and the target is not known to be idempotent -- blind re-execution would
    risk a duplicate side effect, so this is surfaced as FAILED_RETRYABLE instead of
    silently re-attempted."""


class RunAlreadyAbandoned(ReasoningError):
    """A resume/completion arrived for a run whose lifecycle_state is already
    ABANDONED. Every resume path re-reads lifecycle_state first and refuses late
    external completions rather than resuming them."""


class AbandonmentBlocked(ReasoningError):
    """A sweeper or an operator tried to abandon a run that still has unresolved
    external work referencing it (see abandonment.py's five preconditions). Carries the
    blocking reference so it can be surfaced for operator action instead of retried
    blindly."""

    def __init__(self, run_id: str, *, blocking_reference: str) -> None:
        super().__init__(f"Run {run_id!r} cannot be abandoned: blocked by {blocking_reference}")
        self.run_id = run_id
        self.blocking_reference = blocking_reference
