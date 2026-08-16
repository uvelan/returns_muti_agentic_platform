"""Closing AI interceptions whose deadline has passed.

The fifth debris class, and the second whose debris is *business* state rather
than infrastructure a test left behind. An interception is written `PENDING`
with an `expires_at` on it; until this existed nothing ever came back to settle
the lapsed ones, so they accumulated as `PENDING` documents that had stopped
meaning anything -- 48 of 49 rows on this deployment, the oldest three days old.
An operator, or an automated harness, taking "the first PENDING" was handed a
request whose caller had long since gone.

**`EXPIRED` already existed; only the transition was missing.**
`InterceptionStatus.EXPIRED` is modelled, `is_terminal` treats it as distinct
from `CANCELLED`, `DurableInterceptionPolicy._verdict_for` maps it to `REJECT`,
and `DurableInterceptionProvider` already closes its *own* record that way when
it gives up waiting. What had no owner were the records no caller is left to
close: a provider whose process died mid-wait, and every request the
non-blocking `DurableInterceptionPolicy` opened, which by design has no
coroutine parked on it to come back and settle it.

**Nothing here decides what is expired.** The predicate
(`ai.interception.store.lapsed_filter`) and the compare-and-set that applies it
live on the store beside the four other transitions of the same state machine --
this module is the schedule, the batch size and the report, exactly as
`OrderLineReservationReclaimer` is the schedule around
`CaseRepository.expire_due_reservations`. A second copy of "PENDING and past its
deadline" here would be a second thing that can disagree with the read path that
hides them.

**The sweep is not the only defence, and it is not meant to be.**
`InterceptionStore.list_pending` already excludes a lapsed record whichever side
of an interval it falls, so an operator never sees one even if this reclaimer is
disabled, behind, or not deployed. What the sweep buys is the settled record --
a status that says `EXPIRED` rather than a `PENDING` that is not true -- and a
collection that stops growing. That is why it is safe for this reclaimer to be
late: it can be late without being wrong.

**No minimum age, like the reservation sweep and unlike the other three.** An
interception carries its own deadline, stamped from the TTL that was live when
it was opened, so the window is already someone's decision. A second age window
here could only keep a dead request in the queue's underlying collection for
longer than the TTL it was written with said it would be.
"""

from __future__ import annotations

import logging
from typing import Protocol

from return_platform.housekeeping.reclamation import ReclamationOutcome

__all__ = ["RESOURCE_CLASS", "InterceptionExpiryReclaimer", "InterceptionSweep"]

logger = logging.getLogger("return_platform.housekeeping.interception_expiry")

RESOURCE_CLASS = "ai-interception"


class InterceptionSweep(Protocol):
    """The two store reads this reclaimer drives.

    Structural, so the reclaimer's rules can be exercised without a Mongo client
    and so this module does not import the interception store -- the arrangement
    every other reclaimer here uses for its store.
    """

    async def count_lapsed(self, *, limit: int) -> int: ...

    async def expire_lapsed(self, *, limit: int) -> int: ...


class InterceptionExpiryReclaimer:
    """Marks lapsed `PENDING` interceptions `EXPIRED`, a bounded batch per pass.

    Deployed everywhere, unlike the probe-database and Temporal reclaimers. Those
    are gated because they delete infrastructure; this moves a lapsed hold from
    one terminal-bound status to the terminal one it should already have had, on
    a collection whose whole purpose is to be read by an operator. There is no
    environment in which leaving a dead request `PENDING` is the better answer.
    """

    def __init__(self, *, sweep: InterceptionSweep, batch_limit: int) -> None:
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        self._sweep = sweep
        self._batch_limit = batch_limit

    async def reclaim_once(self) -> ReclamationOutcome:
        """Count the candidates, settle up to the batch, and report both.

        The count is taken first and on the same predicate, so `examined` is the
        backlog this pass saw rather than a restatement of what it managed to
        move. The two can legitimately differ: an operator answering one of the
        very records this pass selected wins the conditional update, and the
        loser is simply not counted -- the same rule the operator plays by from
        the other side. That difference is reported as `contended` rather than as
        a failure, because nothing went wrong when it happens.
        """
        examined = await self._sweep.count_lapsed(limit=self._batch_limit)
        if examined == 0:
            return ReclamationOutcome(resource_class=RESOURCE_CLASS)
        reclaimed = await self._sweep.expire_lapsed(limit=self._batch_limit)
        contended = max(0, examined - reclaimed)
        if contended:
            logger.info(
                "housekeeping_interception_sweep_contended",
                extra={"examined": examined, "expired": reclaimed, "contended": contended},
            )
        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=examined,
            reclaimed=reclaimed,
            details={"contended": contended},
        )
