"""Which processes are running which configuration release, and what that means.

`ACTIVATED != LIVE`. Promoting a release to RELEASED moves the graph pointer;
it does not move the API process, the five workers, or the model the Order Agent
actually calls. Until every required process class has reported adopting it, the
platform is running two releases at once, and the only honest thing to say about
the new one is *which processes have it and which do not*.

That distinction is the whole point of this module, so it is modelled rather
than implied:

* `ProcessAdoptionRecord` is one process's statement -- class, identity, adopted
  release, adopted revision, when it adopted, when it last said so. Contract C5.
* Records expire. A process that stopped reporting is not adopted-and-quiet, it
  is gone, and a TTL index is what makes silence read as absence rather than as
  a stale yes.
* `evaluate_release_adoption` turns the live records into one of three answers
  about the activated release, and names the classes it is still waiting on.

A class counts as adopted only when **every live instance of it** reports the
activated revision. Two replicas where one lagged would otherwise report a class
as adopted while half its work still ran on the previous release -- which is the
failure this whole area exists to remove, one level down.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

__all__ = [
    "API_PROCESS_CLASS",
    "PROCESS_ADOPTIONS_COLLECTION",
    "REQUIRED_PROCESS_CLASSES",
    "MongoProcessAdoptionStore",
    "ProcessAdoptionRecord",
    "ProcessAdoptionStore",
    "ProcessClassAdoption",
    "ReleaseAdoptionState",
    "ReleaseAdoptionStatus",
    "adoption_record_from_snapshot",
    "evaluate_release_adoption",
]

PROCESS_ADOPTIONS_COLLECTION = "runtime_process_adoptions"
"""One document per live process instance, `_id = "<class>:<instance>"`.

Deliberately not the `worker_heartbeats` document, which is keyed by class alone
and therefore cannot hold two replicas. Readiness asks "is this class up", which
one row per class answers; adoption asks "is *every* instance on the new
release", which it cannot.
"""

API_PROCESS_CLASS = "api"
"""The FastAPI process's class, named once so the reporter and the required set
cannot disagree about what to call it."""

REQUIRED_PROCESS_CLASSES: frozenset[str] = frozenset(
    {
        API_PROCESS_CLASS,
        "return-workflow-worker",
        "order-discovery-worker",
        "return-orchestrator",
        "outbox-publisher",
        "integration-outbox-worker",
    }
)
"""The process classes a release must reach before it is live.

These are the identifiers the processes already publish -- the same strings
their heartbeats use -- not new names invented here. The API process is in the
set because it holds its own snapshot and serves reads from it; a release
adopted by every worker and not by the API is exactly as split as the reverse.

`data-job-worker` is deliberately absent even though `compose.yaml` deploys it:
`scripts/run_data_job_worker.py` imports `return_platform.data_console`, which
does not exist in this repository, so the container cannot start. Listing a
class that can never report would make every release permanently not-live and
turn a real signal into one operators learn to ignore. Add it here the moment
that import resolves.
"""


class ReleaseAdoptionStatus(StrEnum):
    """What can truthfully be said about the activated release."""

    #: Every required class has at least one live instance and all of them
    #: report the activated revision.
    LIVE = "LIVE"
    #: The release is activated and at least one required class has not adopted
    #: it yet -- either because it is still on the previous one or because no
    #: live instance of it is reporting at all.
    ACTIVATING = "ACTIVATING"
    #: No release is activated, so there is nothing for a process to adopt.
    NO_ACTIVE_RELEASE = "NO_ACTIVE_RELEASE"


class ProcessAdoptionRecord(BaseModel):
    """One long-running process's report. Contract C5, in full."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    process_class: str
    instance_id: str
    release_id: str
    head_revision: int
    #: When this process swapped onto the release it is reporting. Distinct from
    #: `reported_at`: the gap between them is how long the process has been
    #: quietly serving it, and a process that adopted an hour ago and reported a
    #: second ago is healthy, not stale.
    adopted_at: datetime
    reported_at: datetime
    #: Where the process got the release -- the same vocabulary
    #: `PinnedConfigurationSnapshot.source` uses, so a process running the
    #: version-controlled baseline is not silently counted as having adopted a
    #: graph release.
    source: str

    @property
    def key(self) -> str:
        return f"{self.process_class}:{self.instance_id}"


class ProcessClassAdoption(BaseModel):
    """The adoption answer for one process class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    process_class: str
    required: bool
    adopted: bool
    #: Live instances of this class, and how many of them are on the activated
    #: revision. `live_instances == 0` is why `adopted` is false for a class
    #: nothing is running -- reported separately so an operator can tell "not
    #: deployed" from "deployed and behind".
    live_instances: int
    adopted_instances: int
    instances: tuple[ProcessAdoptionRecord, ...] = ()


class ReleaseAdoptionState(BaseModel):
    """`ACTIVATED != LIVE`, as one answer an operator can act on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReleaseAdoptionStatus
    activated_release_id: str | None
    activated_head_revision: int | None
    #: Named, because "not live" without saying what it is waiting for is not
    #: actionable. A class appears here whether it is behind or absent.
    pending_process_classes: tuple[str, ...]
    process_classes: tuple[ProcessClassAdoption, ...]
    evaluated_at: datetime


class ProcessAdoptionStore(Protocol):
    """What reporting and reading need, and nothing else."""

    async def report(self, record: ProcessAdoptionRecord, *, ttl_seconds: int) -> None: ...

    async def list_live(self) -> tuple[ProcessAdoptionRecord, ...]: ...


class MongoProcessAdoptionStore:
    """Platform-Mongo backed. One document per instance, expired by the server.

    The TTL is set from the reporting cadence rather than being a constant here:
    a process that reports every five seconds and one that reports every minute
    should not share a definition of "recently enough", and the reporter is the
    only thing that knows its own interval.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("expiresAt", expireAfterSeconds=0)
        await self._collection.create_index("processClass")

    async def report(self, record: ProcessAdoptionRecord, *, ttl_seconds: int) -> None:
        payload = {
            "processClass": record.process_class,
            "instanceId": record.instance_id,
            "releaseId": record.release_id,
            "headRevision": record.head_revision,
            "adoptedAt": record.adopted_at,
            "reportedAt": record.reported_at,
            "source": record.source,
            # Three intervals, matching `worker_heartbeats`: one missed report
            # is a slow poll, three is a process that is gone.
            "expiresAt": record.reported_at + timedelta(seconds=max(1, ttl_seconds) * 3),
        }
        await self._collection.update_one({"_id": record.key}, {"$set": payload}, upsert=True)

    async def list_live(self) -> tuple[ProcessAdoptionRecord, ...]:
        """Every unexpired report.

        Mongo's TTL monitor runs about once a minute, so a document can outlive
        its `expiresAt` briefly. Filtered here as well, because an operator
        reading "live" during that window would otherwise be told a stopped
        process is still serving the release.
        """

        now = datetime.now(UTC)
        cursor = self._collection.find({"expiresAt": {"$gt": now}})
        return tuple([_record_of(document) async for document in cursor])


def _record_of(document: Mapping[str, Any]) -> ProcessAdoptionRecord:
    return ProcessAdoptionRecord(
        process_class=str(document["processClass"]),
        instance_id=str(document["instanceId"]),
        release_id=str(document["releaseId"]),
        head_revision=int(document["headRevision"]),
        adopted_at=_utc(document["adoptedAt"]),
        reported_at=_utc(document["reportedAt"]),
        source=str(document["source"]),
    )


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("adoption timestamps must be datetimes")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def evaluate_release_adoption(
    *,
    activated_release_id: str | None,
    activated_head_revision: int | None,
    records: Iterable[ProcessAdoptionRecord],
    required_process_classes: Sequence[str] | frozenset[str] = REQUIRED_PROCESS_CLASSES,
    evaluated_at: datetime | None = None,
) -> ReleaseAdoptionState:
    """Decide whether the activated release is actually live.

    Compares the *revision* as well as the release id. A release id can be
    re-pointed while the head moves underneath it, and a process that reports
    the right name at the wrong revision has not adopted what is activated.
    """

    now = evaluated_at or datetime.now(UTC)
    required = frozenset(required_process_classes)
    by_class: dict[str, list[ProcessAdoptionRecord]] = {}
    for record in records:
        by_class.setdefault(record.process_class, []).append(record)

    if activated_release_id is None or activated_head_revision is None:
        return ReleaseAdoptionState(
            status=ReleaseAdoptionStatus.NO_ACTIVE_RELEASE,
            activated_release_id=activated_release_id,
            activated_head_revision=activated_head_revision,
            pending_process_classes=tuple(sorted(required)),
            process_classes=tuple(
                _class_adoption(
                    process_class,
                    by_class.get(process_class, []),
                    required=process_class in required,
                    activated_release_id=None,
                    activated_head_revision=None,
                )
                for process_class in sorted(required | by_class.keys())
            ),
            evaluated_at=now,
        )

    adoptions = tuple(
        _class_adoption(
            process_class,
            by_class.get(process_class, []),
            required=process_class in required,
            activated_release_id=activated_release_id,
            activated_head_revision=activated_head_revision,
        )
        for process_class in sorted(required | by_class.keys())
    )
    pending = tuple(
        adoption.process_class
        for adoption in adoptions
        if adoption.required and not adoption.adopted
    )
    return ReleaseAdoptionState(
        status=(ReleaseAdoptionStatus.LIVE if not pending else ReleaseAdoptionStatus.ACTIVATING),
        activated_release_id=activated_release_id,
        activated_head_revision=activated_head_revision,
        pending_process_classes=pending,
        process_classes=adoptions,
        evaluated_at=now,
    )


def _class_adoption(
    process_class: str,
    records: Sequence[ProcessAdoptionRecord],
    *,
    required: bool,
    activated_release_id: str | None,
    activated_head_revision: int | None,
) -> ProcessClassAdoption:
    matching = [
        record
        for record in records
        if record.release_id == activated_release_id
        and record.head_revision == activated_head_revision
    ]
    return ProcessClassAdoption(
        process_class=process_class,
        required=required,
        # Every live instance, not any: one replica left behind is one replica
        # still serving the previous release. A class with nothing running has
        # not adopted either -- there is no instance to have done so.
        adopted=(
            activated_release_id is not None and len(records) > 0 and len(matching) == len(records)
        ),
        live_instances=len(records),
        adopted_instances=len(matching),
        instances=tuple(sorted(records, key=lambda record: record.instance_id)),
    )


def adoption_record_from_snapshot(
    *,
    process_class: str,
    instance_id: str,
    snapshot: Any,
    reported_at: datetime | None = None,
) -> ProcessAdoptionRecord:
    """Read a report straight off the snapshot the process is running.

    Deriving it rather than letting each process assemble one is what keeps the
    reported release identical to the served release: there is no second place
    for a process to say what it is running.
    """

    now = reported_at or datetime.now(UTC)
    return ProcessAdoptionRecord(
        process_class=process_class,
        instance_id=instance_id,
        release_id=str(snapshot.release_id),
        head_revision=int(snapshot.head_revision),
        adopted_at=_utc(snapshot.loaded_at),
        reported_at=now,
        source=str(snapshot.source),
    )
