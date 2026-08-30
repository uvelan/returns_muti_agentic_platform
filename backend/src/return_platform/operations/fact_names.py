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

#: One inbound support message, recorded on the case at the moment its analysis
#: commits (contracts.md sect. 5). Case-level: a message is addressed to the
#: case, and the *records* it turns out to be about are the record-scoped facts
#: written beside it.
SUPPORT_MESSAGE_RECEIVED: Final[str] = "support_message_received"

#: The accepted classification of one inbound message, from the closed sect. 5
#: taxonomy. Written only from `accepted_classification`, so the fact on the
#: case and the analysis record's committed answer can never disagree.
SUPPORT_MESSAGE_INTENT: Final[str] = "support_message_intent"

#: A question the platform must put to the associate before it can act on a
#: message (contracts.md sect. 9): an unmatched or ambiguous artifact, carrying
#: the value, the evidence span, the candidate records and the map-or-reject
#: choice. V3 owns the answer flow; V2 only writes the question.
SUPPORT_CLARIFICATION_REQUESTED: Final[str] = "support_clarification_requested"

#: One case's resolution spend has reached `support_resolver.per_case_llm_budget`
#: (contracts.md sect. 9). Written by the ladder's escalation node at the moment
#: the budget stops it, so the case carries the reason it stopped answering --
#: exhaustion is visible work, never a silent halt.
SUPPORT_RESOLVER_BUDGET_EXHAUSTED: Final[str] = "support_resolver_budget_exhausted"

#: An answer the resolver composed for a support question (contracts.md
#: sect. 9). Written on **both** gate paths -- the reviewed one and the
#: `auto_reply` one -- because the fact records that the platform composed an
#: answer, which is equally true whichever way it left. Case-level: a reply
#: answers the message, and the records it concerns are scoped facts beside it.
SUPPORT_REPLY_DRAFT: Final[str] = "support_reply_draft"
