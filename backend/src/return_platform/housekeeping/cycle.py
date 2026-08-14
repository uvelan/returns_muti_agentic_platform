"""One housekeeping pass across every debris class, and the loop that repeats it.

Composition, not policy. Each reclaimer owns its own eligibility rules; this
module owns only the order they run in, the isolation between them, and the fact
that a pass which cannot run one of them still runs the others.

**Reclaimers are rebuilt from configuration on every pass.** Not held from
startup, because the construction-time safety checks -- the Temporal reclaimer's
refusal of a prefix matching a deployed queue, the probe reclaimer's refusal of a
suffix matching the application's database -- are what make a bad release
harmless, and they only fire when a reclaimer is built. Rebuilding per pass means
a release that would widen a rule dangerously fails to produce a reclaimer at all
and this cycle records `skipped`; holding one from startup would mean the release
was validated once, against the release that was live at boot.

That is deliberately the fail-closed direction: a configuration this cannot
validate reclaims nothing, and says so, rather than reclaiming on rules nobody
checked.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from return_platform.housekeeping.reclamation import ReclamationOutcome

__all__ = ["HousekeepingCycle", "HousekeepingPass", "run_housekeeping_loop"]

logger = logging.getLogger("return_platform.housekeeping.cycle")


class Reclaimer(Protocol):
    """Structural: anything with `reclaim_once`."""

    async def reclaim_once(self) -> ReclamationOutcome: ...


#: A reclaimer factory, so a pass builds from the configuration that is live
#: *now*. Returning `None` means "not configured for this pass", which is how a
#: disabled block reaches the report. Raising means the release could not be
#: validated, which is how a dangerous one does.
ReclaimerFactory = Callable[[], "Reclaimer | None"]


@dataclass(frozen=True, slots=True)
class HousekeepingPass:
    """Every reclaimer's outcome from one pass."""

    outcomes: tuple[ReclamationOutcome, ...]

    @property
    def reclaimed(self) -> int:
        return sum(outcome.reclaimed for outcome in self.outcomes)

    @property
    def failed(self) -> int:
        return sum(outcome.failed for outcome in self.outcomes)


class HousekeepingCycle:
    """Runs each reclaimer once, independently."""

    def __init__(self, factories: Sequence[tuple[str, ReclaimerFactory]]) -> None:
        self._factories = tuple(factories)

    async def run_once(self) -> HousekeepingPass:
        outcomes: list[ReclamationOutcome] = []
        for resource_class, factory in self._factories:
            try:
                reclaimer = factory()
            except Exception as exc:  # noqa: BLE001 - a bad release must not reap
                # The construction-time safety checks land here. A release whose
                # rules cannot be validated produces no reclaimer and no
                # deletions, and the reason is on the outcome rather than only in
                # a traceback.
                logger.error(
                    "housekeeping_reclaimer_not_constructible",
                    extra={"resource_class": resource_class, "error_type": type(exc).__name__},
                    exc_info=True,
                )
                outcomes.append(
                    ReclamationOutcome.skipped(resource_class, f"configuration rejected: {exc}")
                )
                continue
            if reclaimer is None:
                outcomes.append(ReclamationOutcome.skipped(resource_class, "disabled"))
                continue
            try:
                outcome = await reclaimer.reclaim_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one class never stops the pass
                logger.error(
                    "housekeeping_reclamation_pass_failed",
                    extra={"resource_class": resource_class, "error_type": type(exc).__name__},
                    exc_info=True,
                )
                outcomes.append(ReclamationOutcome.skipped(resource_class, f"pass failed: {exc}"))
                continue
            outcomes.append(outcome)
            logger.info(
                "housekeeping_reclamation_pass",
                extra={
                    "resource_class": outcome.resource_class,
                    "examined": outcome.examined,
                    "reclaimed": outcome.reclaimed,
                    "failed": outcome.failed,
                    "skipped_reason": outcome.skipped_reason,
                    **outcome.details,
                },
            )
        return HousekeepingPass(outcomes=tuple(outcomes))


async def run_housekeeping_loop(
    cycle: HousekeepingCycle,
    *,
    interval_seconds: Callable[[], float],
    before_pass: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Repeat the cycle forever.

    `interval_seconds` is a callable, not a number: the interval is configuration
    and this loop must adopt a released change to it without a restart, like the
    outbox publisher reads its retention once per flush. Read between passes, so
    a pass already running keeps the cadence it started under.

    A pass that raises is logged and retried. `HousekeepingCycle.run_once`
    already isolates each reclaimer, so reaching this handler means the cycle
    itself failed -- which is still not a reason to stop reclaiming.
    """

    while True:
        try:
            if before_pass is not None:
                await before_pass()
            result = await cycle.run_once()
            # One aggregate line per pass, on top of the per-reclaimer lines. It
            # is what an operator greps for to answer "is housekeeping doing
            # anything", which no individual reclaimer's line can answer.
            logger.info(
                "housekeeping_pass_complete",
                extra={
                    "reclaimed": result.reclaimed,
                    "failed": result.failed,
                    "skipped": sum(1 for outcome in result.outcomes if not outcome.ran),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a housekeeper that dies stops housekeeping
            logger.error("housekeeping_cycle_failed", exc_info=True)
        await asyncio.sleep(max(1.0, interval_seconds()))
