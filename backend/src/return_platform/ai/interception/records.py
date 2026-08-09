"""What a held AI request looks like while a human is answering it.

Phase 14 replaces the filesystem handoff `ManualFileProvider` uses -- a JSON file
written to `.manual_llm/requests/` and a reply polled out of
`.manual_llm/responses/`. That mechanism has no durability story at all: the
directory is relative to the process CWD, a restart loses every in-flight
request, two replicas do not see each other's files, and nothing records that a
human rather than a model produced the answer.

**The record is one document, and that is the atomicity argument.** The plan
requires the interception and its embedded resume command to be persisted
atomically; keeping the request, the status, the answer and the resume command
in a single document makes that MongoDB's single-document write guarantee rather
than a transaction that could be forgotten.

**Two fields exist to prevent misattribution, not for bookkeeping.**
`answered_by` names the human, and the provider that returns the answer reports
`MANUAL` / `manual-human-v1`. A human answer must never be recorded as though a
model produced it -- an evaluation set built from traces would silently be
training on and measuring human text.

**No chain of thought, deliberately.** The record holds the request that was
sent and the response text that came back. There is no field for reasoning,
working, or deliberation, and the sealed payload is the request as the provider
would have sent it -- not an expanded trace of how anyone arrived at an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InterceptionStatus(StrEnum):
    """PENDING -> ANSWERED, or -> CANCELLED / EXPIRED.

    `EXPIRED` is distinct from `CANCELLED` because they mean different things to
    an operator: expired is "nobody got to it in time", cancelled is "the run
    that needed it went away". Collapsing them would hide a staffing problem
    behind what looks like ordinary churn.
    """

    PENDING = "PENDING"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


_TERMINAL = frozenset(
    {InterceptionStatus.ANSWERED, InterceptionStatus.CANCELLED, InterceptionStatus.EXPIRED}
)


def is_terminal(status: InterceptionStatus) -> bool:
    return status in _TERMINAL


@dataclass(frozen=True, slots=True)
class ResumeCommand:
    """How the waiting work is resumed once a human answers.

    Stored *with* the interception rather than derived at answer time: the
    process that opened the interception knows what needs resuming, and the
    process that delivers the answer may be a different one entirely (an
    operator console, a worker). Deriving it later would mean reconstructing
    context the answering side does not have.
    """

    run_id: str
    thread_id: str
    workflow_id: str | None = None


@dataclass(frozen=True, slots=True)
class Interception:
    """One held AI request. The request payload itself is sealed at rest."""

    interception_id: str
    task_id: str
    status: InterceptionStatus
    resume: ResumeCommand
    created_at: datetime
    expires_at: datetime
    answered_at: datetime | None = None
    answered_by: str | None = None
    response_text: str | None = None
