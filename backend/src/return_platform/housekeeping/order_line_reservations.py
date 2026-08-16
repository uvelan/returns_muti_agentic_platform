"""Settling order-line holds whose TTL has elapsed (plan sect. 12.3).

The fourth debris class, and the only one whose debris is *business* state rather
than infrastructure left behind by a test. A hold is written `ACTIVE` with an
`expiresAt` on it; nothing in the request path ever comes back to mark the
lapsed ones `EXPIRED`, so without this they accumulate as `ACTIVE` documents that
stopped meaning anything.

**Nothing here decides what is expired.** The predicate, the conditional update
that applies it, and the transition rules it must not break all live on
`CaseRepository.expire_due_reservations` beside the state machine they belong to
-- this module is the schedule, the batch size and the report, exactly as
`GraphGenerationReclaimer` is the schedule around the lease store. A second copy
of "ACTIVE and past its deadline" here would be a second thing that can disagree
with the update that enforces it.

**The arithmetic never waits for this pass.** `is_held` already treats an
`ACTIVE` hold past its deadline as holding nothing, so an abandoned selection
releases its quantity at the deadline whether or not this worker is deployed.
What the sweep buys is the settled record -- an audit trail that says `EXPIRED`
rather than a stale `ACTIVE` -- and a partial unique index that will accept a
fresh hold on a line the same case abandoned. That is why it is safe for this
reclaimer to be disabled, skipped or behind: it can be late without being wrong.

**No minimum age, unlike the other three reclaimers.** They infer abandonment
from how long a resource has existed, because nothing on the resource says when
it stopped being wanted. A reservation carries its own deadline, stamped from the
release that was live when it was taken, and a second age window here would be a
second answer to "is this hold over" -- one that could hold quantity out of a
branch's reach for longer than the operator's TTL said it would.
"""

from __future__ import annotations

import logging
from typing import Protocol

from return_platform.housekeeping.reclamation import ReclamationOutcome

__all__ = ["RESOURCE_CLASS", "OrderLineReservationReclaimer", "ReservationSweep"]

logger = logging.getLogger("return_platform.housekeeping.order_line_reservations")

RESOURCE_CLASS = "order-line-reservation"


class ReservationSweep(Protocol):
    """The two repository reads this reclaimer drives.

    Structural, so the reclaimer's rules can be exercised without a Mongo client
    and so this module does not import the repository -- the arrangement every
    other reclaimer here uses for its store.
    """

    async def count_due_reservations(self, *, limit: int) -> int: ...

    async def expire_due_reservations(self, *, limit: int) -> int: ...


class OrderLineReservationReclaimer:
    """Marks lapsed `ACTIVE` holds `EXPIRED`, a bounded batch per pass.

    Deployed everywhere, unlike the probe-database and Temporal reclaimers: this
    is ordinary bookkeeping on production data and there is no environment in
    which leaving lapsed holds `ACTIVE` is the better answer.
    """

    def __init__(self, *, sweep: ReservationSweep, batch_limit: int) -> None:
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        self._sweep = sweep
        self._batch_limit = batch_limit

    async def reclaim_once(self) -> ReclamationOutcome:
        """Count the candidates, settle up to the batch, and report both.

        The count is taken first and on the same predicate, so `examined` is the
        backlog this pass saw rather than a restatement of what it managed to
        move. The two can legitimately differ: an authorization consuming one of
        the very holds this pass selected wins the conditional update, and the
        loser is simply not counted -- the same rule the authorization plays by
        from the other side. That difference is reported as `contended` rather
        than as a failure, because nothing went wrong when it happens.
        """
        examined = await self._sweep.count_due_reservations(limit=self._batch_limit)
        if examined == 0:
            return ReclamationOutcome(resource_class=RESOURCE_CLASS)
        reclaimed = await self._sweep.expire_due_reservations(limit=self._batch_limit)
        contended = max(0, examined - reclaimed)
        if contended:
            logger.info(
                "housekeeping_reservation_sweep_contended",
                extra={"examined": examined, "expired": reclaimed, "contended": contended},
            )
        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=examined,
            reclaimed=reclaimed,
            details={"contended": contended},
        )
