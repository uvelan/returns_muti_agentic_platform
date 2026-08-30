"""One reading of what the workflow host just told us (RV V1p2-2, A4).

Two surfaces on this branch ask Temporal the same question -- the panel, to
decide whether to degrade its execution block, and the recovery endpoint, to
decide whether a redelivery would be applied. They had **two different error
vocabularies for the same status codes**: the panel already treated `NOT_FOUND`
as a normal informative answer, while the endpoint collapsed every `RPCError`
into "cannot tell" and told an operator to come back later about a workflow that
definitively does not exist.

So the classification lives here, once, and both call it.

**The distinction that matters is not "error or not" -- it is "definitive or
not".** A host that answered `NOT_FOUND` has told us something true and stable:
there is no such execution, and there will not be one in thirty seconds. A host
that timed out has told us nothing. Those two send an operator to opposite
actions -- one to the exit, one back to the button -- so collapsing them is a
wrong answer rather than a rounding.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from temporalio.service import RPCError, RPCStatusCode

__all__ = ["ExecutionAnswer", "classify_execution_failure"]


class ExecutionAnswer(StrEnum):
    """What a failed `execution_state` query means."""

    #: The host answered: there is no such execution. **Definitive.**
    ABSENT = "ABSENT"
    #: The host did not answer. Says nothing about the execution. **Transient.**
    UNREACHABLE = "UNREACHABLE"


#: Statuses that mean "the host is having a bad minute", not "the answer is no".
_UNREACHABLE_STATUSES: Final[frozenset[RPCStatusCode]] = frozenset(
    {RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED}
)


def classify_execution_failure(error: Exception) -> ExecutionAnswer | None:
    """Read one failure, or `None` to say **this is not ours -- re-raise it**.

    `None` is the load-bearing return. A `PERMISSION_DENIED` from the workflow
    host is a real problem somebody has to fix, and a caller that rendered it as
    "temporarily unavailable" would hide it for as long as anybody was willing
    to keep refreshing. Only the two shapes above are absorbed; everything else
    is somebody's incident.
    """
    if isinstance(error, (TimeoutError, ConnectionError)):
        return ExecutionAnswer.UNREACHABLE
    if isinstance(error, RPCError):
        if error.status is RPCStatusCode.NOT_FOUND:
            return ExecutionAnswer.ABSENT
        if error.status in _UNREACHABLE_STATUSES:
            return ExecutionAnswer.UNREACHABLE
    return None
