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

**One record, two points.** The same document now also holds a *response* a
human is reviewing, distinguished by `InterceptionPoint`. Everything that made
this record worth having -- one atomic document, sealed at rest, compare-and-set
transitions, one operator queue -- is exactly as valuable for a held response,
and a parallel store would have had to re-earn all of it.

**No chain of thought, deliberately.** The record holds the request that was
sent and the response text that came back. There is no field for reasoning,
working, or deliberation, and the sealed payload is the request as the provider
would have sent it -- not an expanded trace of how anyone arrived at an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InterceptionPoint(StrEnum):
    """*When* a human was put in the loop, which is the only thing that differs.

    There are two moments worth holding at, and they are genuinely different
    jobs. At `REQUEST` the provider has not been called: a human writes an answer
    from scratch, and the model never runs. At `RESPONSE` the provider has
    already answered: a human reads what came back and either takes it, changes
    it, or throws it away.

    A field on the same record rather than a second collection. The queue, the
    sealing, the compare-and-set discipline, the expiry sweep and the operator
    endpoints are identical for both; the only thing that varies is what the
    sealed payload contains and how the three terminal statuses are *read*:

        point     ANSWERED          ALLOWED                CANCELLED/EXPIRED
        REQUEST   human wrote it    model may proceed      request refused
        RESPONSE  human edited it   response accepted      response rejected

    Reusing the statuses instead of adding `ACCEPTED`/`EDITED`/`REJECTED` keeps
    one state machine. Eight statuses whose legality depended on a sibling field
    would be two state machines wearing one enum, and every consumer would have
    to learn both.
    """

    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class InterceptionStatus(StrEnum):
    """PENDING -> ANSWERED / ALLOWED, or -> CANCELLED / EXPIRED.

    `EXPIRED` is distinct from `CANCELLED` because they mean different things to
    an operator: expired is "nobody got to it in time", cancelled is "the run
    that needed it went away". Collapsing them would hide a staffing problem
    behind what looks like ordinary churn.

    `ALLOWED` is distinct from `ANSWERED` for the same reason, and it is the one
    the platform did not model: an operator could substitute a human answer
    (`ANSWERED`) or kill the request (`CANCELLED`), but "I have looked at this
    and the model may proceed unchanged" had no representation at all. Without it
    interception can only ever divert a request, never approve one, and the three
    outcomes C7 requires -- `ALLOW_PROVIDER | HUMAN_RESPONSE | REJECT` -- collapse
    to two. It is also what distinguishes the provider's own output from a
    human's in every downstream record: an `ALLOWED` request is answered by the
    model and reported as the model, an `ANSWERED` one is reported as MANUAL.
    """

    PENDING = "PENDING"
    ANSWERED = "ANSWERED"
    ALLOWED = "ALLOWED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


_TERMINAL = frozenset(
    {
        InterceptionStatus.ANSWERED,
        InterceptionStatus.ALLOWED,
        InterceptionStatus.CANCELLED,
        InterceptionStatus.EXPIRED,
    }
)

#: Statuses that mean the held work may now continue. Both must reach the resume
#: dispatcher: an approved request is as blocked as an answered one until its run
#: is signalled.
RESUMABLE = frozenset({InterceptionStatus.ANSWERED, InterceptionStatus.ALLOWED})


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
    #: Defaulted to `REQUEST` so every record written before response
    #: interception existed reads as what it was. An absent field must not become
    #: an ambiguous one.
    point: InterceptionPoint = InterceptionPoint.REQUEST
