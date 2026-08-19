# Shutdown

**Current as of 2026-08-14, commit `dcbb7dc`.**

## Order matters

Stop the application before the infrastructure. A worker that loses Temporal or
Mongo mid-activity produces retry noise and, on the shipment path, a SQL row whose
graph projection never landed.

```text
1. application processes    scripts/linux/17_stop_host_processes.sh
2. infrastructure           ./scripts/infra.sh stop
```

## Stopping the application

```bash
./scripts/linux/17_stop_host_processes.sh
```

Stops the repository-managed processes: backend, frontend and the workers.

It stops **only** processes this repository started. It closes repository-owned
listeners on `8000` and `5173` and **refuses to terminate unrelated processes** — a
launcher that killed whatever held a port would eventually kill something that
mattered.

`run_all_host.sh` performs the same stop before it starts, so a restart does not
require a separate shutdown.

## Stopping infrastructure

```bash
./scripts/infra.sh stop
```

Stops the containers and **preserves the volumes**. Data survives.

For containerized mode:

```bash
docker compose --profile containerized-app down
```

## What is safe to interrupt

The platform is built so that shutdown at any point is recoverable. Specifics
worth knowing:

| In flight | On shutdown |
|---|---|
| A `ReturnCaseWorkflow` | **Durable.** Temporal resumes it when the worker returns. Business-calendar waits and reminder timers survive |
| A discovery turn | The HTTP request fails. The conversation is durable and the associate retries the turn |
| A confirmation between case commit and workflow start | The case is committed with `workflowId` null. **The recovery sweep starts its workflow on a later pass** — see [`recovery.md`](recovery.md) |
| A graph sync run | The candidate generation is left un-swapped. **The previous generation keeps serving.** The next run starts a fresh candidate |
| An incremental sync mid-batch | The watermark did not advance, so the batch is re-read. Projection writes merge, so re-reading is idempotent |
| A shipment update | Either the SQL transaction committed or it did not. If it committed and the graph projection did not, the caller got a 502 and resubmits — the resubmission answers `DUPLICATE` |
| An outbox message | Undelivered messages remain queued and are retried |
| An interception awaiting a human | Remains held. `interception_resume` delivers the decision when it restarts |
| A seed operation | Cancellable at a safe persistence boundary — `POST /api/v1/seed-data/cancel` |

## What is not safe to interrupt

**A destructive graph cutover, at the compare-and-swap.** The swap itself is atomic,
so an interruption lands on one side or the other — but a cutover interrupted during
the *build* leaves a partially populated candidate generation occupying storage. It
is marked `FAILED` and is not served, so correctness is intact; reclaim the space
before rebuilding.

**A SQL migration.** Migrations are checksum-tracked in `platform.schema_migrations`
and are safe to rerun, but interrupting one mid-statement can leave a partially
applied DDL that the migration runner then refuses because the recorded checksum
does not match. Let migrations finish.

## Graceful worker drain

Workers stop on `SIGTERM`. A worker in the middle of an activity finishes it or
lets Temporal retry it on another worker; a graph reader holding a generation drain
lease releases it.

**Do not `SIGKILL` a worker holding a generation write reservation.** The reservation
is counted against the generation document, and a killed holder leaves it counted
until it expires — which delays a pending retirement rather than corrupting
anything, but delays it for the full lease duration.

## Before a planned shutdown

```bash
# 1. Is anything mid-decision that a human should finish?
curl -fsS http://127.0.0.1:8000/api/ai/interceptions | jq '.data | length'

# 2. Is a sync running?
curl -fsS http://127.0.0.1:8000/api/graph-sync/runs | jq '.data[0]'

# 3. Is a release still activating?
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq '.data.status'
```

An `ACTIVATING` release is not a reason to delay a shutdown — restarted processes
load the active release at startup and adopt it immediately, which is the fastest
possible adoption.

Held interceptions **are** worth checking: each one has a caller blocked behind it,
and shutting down leaves that caller waiting until `interception_resume` restarts.

## Restart

```bash
./scripts/run_all_host.sh
```

Reuses the active graph release. Does not rerun live AI validation.

`scripts/linux/15_restart_and_replay.sh` exercises restart and replay behaviour as
a validation step.

## Related

- [`startup.md`](startup.md)
- [`reset.md`](reset.md)
- [`recovery.md`](recovery.md)
