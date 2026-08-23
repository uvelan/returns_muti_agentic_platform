"""Wait for a workflow result, and say what went wrong when it never comes.

`handle.result()` waits forever by design -- a case may legitimately sit for
days. In a test that is the wrong default twice over. A workflow whose task
fails is retried by the server indefinitely, so the bug that should have failed
one test instead stops the run: no dot, no summary, no totals.

That is not hypothetical. A stale test double returned `str` where the activity
had begun returning `SupportRequestDraft`; the workflow task failed to decode
it, the server re-scheduled the task forever, and the live-infrastructure suite
sat on one test until it was killed. The information needed to diagnose it was
in the history the whole time.

So this bounds the wait and, on expiry, reads the history and reports what the
server saw -- the failed workflow task's message, the pending activities and
their last failure, and the tail of event types. A wedge fails one test with the
reason attached instead of consuming the run.

The ceiling is deliberately far above any timing these tests configure. It is
there to catch a workflow that is not progressing at all, not to police a slow
one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio.client import WorkflowHandle

#: Generous on purpose: the longest wait these tests configure is measured in
#: tens of seconds, so anything beyond this is stuck rather than slow.
DEFAULT_CEILING_SECONDS = 120


async def _diagnose(handle: WorkflowHandle[Any, Any], seconds: int) -> str:
    lines = [
        f"Workflow {handle.id} produced no result within {seconds}s.",
        "It is not progressing. What the server has:",
    ]
    try:
        description = await handle.describe()
        raw = description.raw_description
        lines.append(f"  status            : {description.status}")
        lines.append(f"  pendingActivities : {len(raw.pending_activities)}")
        for pending in raw.pending_activities:
            lines.append(
                f"      {pending.activity_type.name} attempt={pending.attempt}"
            )
            if pending.last_failure.message:
                lines.append(f"        last failure: {pending.last_failure.message[:300]}")

        events = [event async for event in handle.fetch_history_events()]
        lines.append(f"  historyLength     : {len(events)}")

        # A repeating workflow-task failure is the signature of a wedge, and the
        # message on it is almost always the whole answer.
        failures: list[str] = []
        for event in events:
            attributes = event.workflow_task_failed_event_attributes
            message = attributes.failure.message
            if not message or message in failures:
                continue
            failures.append(message)
            lines.append(f"  workflow task failed: {message[:300]}")
            cause = attributes.failure.cause
            if cause.message:
                lines.append(f"        cause: {cause.message[:300]}")
        if not failures:
            tail = ", ".join(str(event.event_type) for event in events[-8:])
            lines.append(f"  no failed workflow task; last event types: {tail}")
    except Exception as exc:  # noqa: BLE001 - a failed diagnosis must not mask the timeout
        lines.append(f"  (could not read history: {type(exc).__name__}: {exc})")
    return "\n".join(lines)


async def result_within(
    handle: WorkflowHandle[Any, Any], seconds: int = DEFAULT_CEILING_SECONDS
) -> Any:
    """`handle.result()`, but a wedge fails this test instead of the run."""
    try:
        return await asyncio.wait_for(handle.result(), timeout=seconds)
    except TimeoutError:
        raise AssertionError(await _diagnose(handle, seconds)) from None
