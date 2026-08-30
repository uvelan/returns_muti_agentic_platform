"""Canonical case-fact names (contracts.md sect. 4).

The single home for the fact-name vocabulary the support bridge writes: a
string literal of one of these names anywhere else in the tree is an RV
blocking finding, precisely so a rename is a one-line change and a typo is an
import error rather than a fact nobody ever reads back.

Deliberately sparse. A constant lands here in the same change as its first
real reference -- an unused name is vocabulary nothing enforces, and the
contract forbids shipping one. Later slices append their own as they earn
them.
"""

from typing import Final

#: A loose artifact that names no return reference on a case holding several
#: records -- it could belong to any of them, and code must not guess
#: (contracts.md sect. 4 binding rules). Scoped to no record by construction.
SUPPORT_ARTIFACT_AMBIGUOUS: Final[str] = "support_artifact_ambiguous"

#: A loose artifact that names a return reference the case does not hold --
#: or that no record could take at all. Never creates a record; it waits for
#: the map-or-reject clarification.
SUPPORT_ARTIFACT_UNMATCHED: Final[str] = "support_artifact_unmatched"

#: An operator's audited decision to skip a dead-lettered predecessor and let
#: its parked case stream resume (contracts.md sect. 7). Written by the skip
#: operation itself, so the decision is on the case the moment it takes
#: effect, with the actor and reason beside it.
SUPPORT_STREAM_SKIP: Final[str] = "support_stream_skip"

#: A persisted compaction summary over earlier case facts, consumed -- never
#: regenerated -- by `assemble_case_context` (contracts.md sect. 10). Recorded
#: with `AcquisitionMethod.CONTEXT_SUMMARY`; compaction never discards the
#: facts it summarises.
CONTEXT_SUMMARY: Final[str] = "context_summary"

#: The rendered outbound draft one review is opened over, scoped by
#: `review_id`. Written when the gate opens the review; a redraft writes
#: another under the new attempt's id, so the fact log holds every draft a case
#: ever produced rather than the last one.
SUPPORT_TEMPLATE_DRAFT: Final[str] = "support_template_draft"

#: A required field the render could not fill (`TemplateGap`), scoped by
#: `review_id`. Review-blocking, and the reason an `on_timeout: auto_send`
#: deployment still holds: a gap is the case saying it does not know something
#: the message claims to state.
SUPPORT_TEMPLATE_GAP: Final[str] = "support_template_gap"

#: A reviewer asking for the draft to be produced again, scoped by `review_id`.
#: Recorded before the re-render rather than after, so a revision requested
#: against a render that then failed is still on the log.
SUPPORT_TEMPLATE_REVISION: Final[str] = "support_template_revision"

#: What was actually sent, scoped by `review_id`: the frozen payload's content
#: hash and the delivery identity it went out under. The audit answer to "what
#: did Support receive", and it is written on the absorbed redelivery too --
#: absorption is delivery (contracts.md sect. 7).
SUPPORT_SENT_SNAPSHOT_REF: Final[str] = "support_sent_snapshot_ref"

#: The draft is rendered and a person can look at it. Case-level, not scoped:
#: it is the signal-side marker contracts.md sect. 7 names, and a case has one
#: answer to "is there something to review".
TEMPLATE_DRAFT_READY: Final[str] = "template_draft_ready"
