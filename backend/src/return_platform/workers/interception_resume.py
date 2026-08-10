"""Run the AI interception resume bridge.

Wave D2. `InterceptionResumeDispatcher` existed, was tested, and **was never
constructed anywhere** — so an answered interception sat at `ANSWERED` forever
and the workflow waiting on it was never signalled. The operator surface added
alongside this would otherwise have been a lie: accept an answer, report
success, resume nothing.

This is a bridge, not a deliverer. It turns answered interceptions into rows in
`reasoning_resume_commands`; `platform/reasoning/resume_worker.py` claims those
under a lease and delivers the real Temporal signals. Two steps rather than one
because at-least-once delivery is already solved there, and a second
implementation of lease-claim-backoff is a second place to get it wrong.

Idle-polls rather than watching a change stream: the interval is bounded by how
long a human is willing to wait after clicking answer, not by throughput, and a
change stream would add a replica-set requirement to a worker that otherwise
needs none.
"""

from __future__ import annotations

import asyncio
import logging

from pymongo import AsyncMongoClient

from return_platform.ai.interception.dispatcher import InterceptionResumeDispatcher
from return_platform.bootstrap.system_store import bootstrap_system_store
from return_platform.configuration.runtime_loader import resolve_process_configuration

logger = logging.getLogger(__name__)

#: How long to wait after finding nothing to enqueue. Short enough that an
#: operator's answer resumes while they are still looking at the screen.
IDLE_SECONDS = 2.0


async def run_forever(
    dispatcher: InterceptionResumeDispatcher, *, idle_seconds: float = IDLE_SECONDS
) -> None:
    """Enqueue answered interceptions until cancelled.

    A failed pass is logged and retried rather than fatal: the dispatcher is
    idempotent by derived `command_id` behind a unique index, so a pass that
    dies halfway replays safely on the next one. Exiting instead would strand
    every subsequent answer on a transient database blip.
    """
    while True:
        try:
            enqueued = await dispatcher.dispatch_once()
        except Exception:  # noqa: BLE001 - a bridge that exits strands every later answer
            logger.exception("interception_resume_pass_failed")
            enqueued = 0
        if not enqueued:
            await asyncio.sleep(idle_seconds)


async def run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    try:
        system_store, _ = await bootstrap_system_store(settings, client)
        await run_forever(InterceptionResumeDispatcher(system_store))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
