"""T19a: reset workflows whose history cannot replay against current code.

**Detected by replaying, not by a list.** Every running `ReturnCaseWorkflow` is
fetched and replayed against the deployed workflow code. A history that replays
is left alone. A history that does not is a wedge, and the replayer names the
reason -- which is the difference between repairing a known defect and repairing
whatever happened to be on a list somebody wrote once.

**Reset, never an edit.** Temporal's reset starts a new run from a chosen point
in the existing history, under current code. Nothing is rewritten; the original
run is preserved and linked. That is the supported mechanic, and it is the only
one the repair rules permit for a wedged execution.

**Why not a `workflow.patched()` guard.** For the wedge this was written for --
`record_case_customer_identity` inserted ahead of an existing
`record_case_status` -- a patch guard is actively wrong. Two populations of
history carry no marker: executions that predate the activity, and executions
that ran after it was added but before any marker existed. `patched()` returns
False for both, so guarding would fix the first and break the second. Two live
histories are in that second group and replay clean today; a guard would wedge
them. Reset touches only the history that is actually broken.

**Reset point.** `WORKFLOW_TASK_STARTED` of the first workflow task, so the
logic re-runs from the top under current code. The activities before the
divergence re-execute -- recording a case status, requesting a bay
recommendation, evaluating eligibility -- and all three are writes the platform
already treats as repeatable.

Dry run by default:

    python scripts/repair_wedged_workflows.py
    python scripts/repair_wedged_workflows.py --apply <digest>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from temporalio.api.enums.v1 import ResetType
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest
from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer

from return_platform.configuration.settings import Settings
from return_platform.workflows.return_case_workflow import ReturnCaseWorkflow

WORKFLOW_TYPE = "return-platform-return-case-v1"


async def _survey(client: Client) -> list[dict[str, Any]]:
    """Replay every running case workflow and record which ones cannot."""
    replayer = Replayer(workflows=[ReturnCaseWorkflow])
    rows: list[dict[str, Any]] = []
    async for description in client.list_workflows(
        f'WorkflowType="{WORKFLOW_TYPE}" AND ExecutionStatus="Running"'
    ):
        handle = client.get_workflow_handle(description.id)
        events = [event async for event in handle.fetch_history_events()]
        first_task_started = next(
            (event.event_id for event in events if event.event_type == 6),
            None,
        )
        row: dict[str, Any] = {
            "workflowId": description.id,
            "runId": description.run_id,
            "startedAt": str(description.start_time),
            "historyLength": len(events),
            "resetToEventId": first_task_started,
        }
        try:
            await replayer.replay_workflow(
                WorkflowHistory(workflow_id=description.id, events=events)
            )
            row["replays"] = True
        except Exception as exc:  # noqa: BLE001 - the reason is the finding
            row["replays"] = False
            row["reason"] = f"{type(exc).__name__}: {str(exc)[:220]}"
        rows.append(row)
    return rows


def _digest(targets: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [
            {
                "workflowId": t["workflowId"],
                "runId": t["runId"],
                "resetToEventId": t["resetToEventId"],
            }
            for t in targets
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write(name: str, payload: dict[str, Any]) -> Path:
    directory = Path(__file__).resolve().parents[2] / ".runtime" / "repair" / "T19a"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="DIGEST", default=None)
    arguments = parser.parse_args()

    settings = Settings()
    client = await Client.connect(
        str(settings.temporal_target),
        namespace=getattr(settings, "temporal_namespace", "default"),
    )

    surveyed = await _survey(client)
    wedged = [row for row in surveyed if not row["replays"]]
    healthy = [row["workflowId"] for row in surveyed if row["replays"]]
    digest = _digest(wedged)

    report: dict[str, Any] = {
        "takenAt": datetime.now(UTC).isoformat(),
        "surveyed": len(surveyed),
        "replayClean": healthy,
        "wedged": wedged,
        "digest": digest,
    }

    if arguments.apply is None:
        path = await asyncio.to_thread(_write, "dry-run", report)
        print(json.dumps(report, indent=2, default=str))
        print(f"\nevidence: {path}")
        if not wedged:
            print("\nNothing wedged.")
            return 0
        print(f"\nTo apply: python scripts/repair_wedged_workflows.py --apply {digest}")
        return 0

    if arguments.apply != digest:
        print(
            "The survey no longer matches the digest given. Re-run the dry run and "
            f"review it.\n  approved : {arguments.apply}\n  current  : {digest}",
            file=sys.stderr,
        )
        return 2

    reset: list[dict[str, Any]] = []
    for row in wedged:
        if row["resetToEventId"] is None:
            reset.append({"workflowId": row["workflowId"], "outcome": "NO_RESET_POINT"})
            continue
        response = await client.workflow_service.reset_workflow_execution(
            ResetWorkflowExecutionRequest(
                namespace=getattr(settings, "temporal_namespace", "default"),
                workflow_execution={
                    "workflow_id": row["workflowId"],
                    "run_id": row["runId"],
                },
                reason="T19a: history cannot replay against deployed code",
                workflow_task_finish_event_id=int(row["resetToEventId"]),
                reset_reapply_type=ResetType.RESET_TYPE_FIRST_WORKFLOW_TASK,
                # Required by the server, and it is the idempotency key: a
                # retried reset with the same id resets once, which matters for
                # an operation that forks a new run.
                request_id=str(uuid.uuid4()),
            )
        )
        reset.append(
            {
                "workflowId": row["workflowId"],
                "fromRunId": row["runId"],
                "newRunId": getattr(response, "run_id", None),
                "outcome": "RESET",
            }
        )

    report["reset"] = reset
    path = await asyncio.to_thread(_write, "applied", report)
    print(json.dumps({"reset": reset}, indent=2, default=str))
    print(f"\nevidence: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
