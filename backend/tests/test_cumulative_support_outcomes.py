"""Support answers repeatedly, and every answer reaches the case (Phase 4).

WHAT WAS BROKEN, MEASURED RATHER THAN INFERRED
----------------------------------------------
`support_response` was first-wins and `run` returned as soon as it had recorded
that one response. A second outcome therefore met a closed execution: the API
answered `500 workflow execution already completed` and the record was left
exactly as the first reply had made it. Cases sit in the platform today with an
RMA and a label and `trackingReference: null`, their workflow `COMPLETED` --
delayed tracking, a delayed label and a corrected RMA could not arrive by any
route.

Two defects, and closing either alone changes nothing. The signal had to
accumulate *and* the writer had to upsert: `record_support_outcome` swallowed
the duplicate-key error on the second reply and `continue`d, so even a notice
that reached the activity wrote nothing.

WHY THE ASSERTIONS ARE WHAT THEY ARE
------------------------------------
**The revision, not a call count.** A client polls `GET /api/cases/{caseId}` and
decides whether to re-render on `revision`. A tracking number that reached the
record without moving the revision is a tracking number no screen will show, so
every arrival is asserted to advance it -- and a *redelivery* is asserted not
to, because a revision that moved over an unchanged projection makes every
client re-fetch forever.

**The projection, not the stored document.** `businessComplete` and `awaiting`
are computed by the shipped `project_case` over the shipped requirement table,
assembled by the shipped `assemble_case_projection_state`. A test that read the
Mongo document and decided for itself what "complete" meant would pass over a
completion rule nobody runs.

**The real activities.** `ReturnCaseActivities` is the shipped class throughout;
only the stores are doubles, and the repository double reproduces
`CaseRepository`'s semantics that this code depends on -- optimistic versions,
the unique `factId`, the unique `(caseId, returnReference)` partial index, and
the plan sect. 6.5 revision bump on every child write.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pymongo.errors import DuplicateKeyError
from temporalio.exceptions import ActivityError

from return_platform.api.cases import _requirement_table as api_requirement_table
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    ReturnPlatformConfiguration,
    build_return_method_requirement_table,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.case_projection.assembly import (
    CaseAggregateDocuments,
    assemble_case_projection_state,
)
from return_platform.operations.case_projection.completion import (
    DEFAULT_RETURN_METHOD_REQUIREMENTS,
)
from return_platform.operations.case_projection.contract import CaseProjection
from return_platform.operations.case_projection.projection import project_case
from return_platform.operations.case_projection.vocabulary import AwaitingDimension
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.workflows import return_case_workflow as workflow_module
from return_platform.workflows.return_case_activities import (
    ReturnCaseActivities,
    ReturnRecordSyncOutcome,
)
from return_platform.workflows.return_case_workflow import (
    CaseTerminalCommand,
    DraftSupportRequestInput,
    PolicyDecisionName,
    PolicyGateState,
    RecordSupportOutcomeInput,
    ReturnCaseOutcome,
    ReturnCaseStatus,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SupportOutcomeReceipt,
    SupportRequestDraft,
    SupportResponseNotice,
    SupportReturnRecord,
    TemplateReviewDraftSet,
    TerminalCommandName,
)

pytestmark = pytest.mark.asyncio

CASE_ID = "case-phase-4"
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

#: The shipped release, and the requirement table built from it.
#:
#: Every fixture below is wired with the configuration rather than with `None`,
#: because the activity's completion read now resolves its requirement table
#: from it. That is the whole point of the change these tests cover: the
#: workflow's "keep waiting or close" used to be computed from
#: `DEFAULT_RETURN_METHOD_REQUIREMENTS` while `GET /api/cases/{caseId}` answered
#: from the release, and the two agreeing was a coincidence of the shipped rows
#: rather than a property of the code.
#:
#: `RELEASED_REQUIREMENTS` is what the repository double projects with, so the
#: projection a test reads and the projection the activity reports come from one
#: table -- which is what makes "the receipt agrees with the API" an assertion
#: about the platform rather than about the fixture.
SHIPPED_CONFIGURATION = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
RELEASED_REQUIREMENTS = build_return_method_requirement_table(SHIPPED_CONFIGURATION)

#: The same release with the policy gate switched back on, and nothing else changed.
#:
#: The shipped file suspends the gate on this development host
#: (`config/returns/production.yaml`, `policy_evaluation.enabled: false`, reason
#: "Suspended on this development host while order-discovery turns are answered
#: through the MANUAL provider"). That is a deployment switch, and it is stated in
#: configuration precisely so it survives a reset -- but it means the shipped
#: release answers `SKIPPED_BY_CONFIGURATION` for every case, which *clears* the
#: gate. A test whose whole claim is "a rejected return opens no work item" then
#: proves nothing: no return is ever rejected, the case sails past the gate, and
#: the assertion that no work item exists holds for a reason the test does not
#: name.
#:
#: `tests/policy/test_case_policy_gate.py` overrides exactly this one value for
#: exactly this reason (see its `configuration` fixture); the reasoning is copied,
#: not re-derived. The skip *itself* is covered over there, against a configuration
#: that says so explicitly.
POLICY_ENABLED_CONFIGURATION = SHIPPED_CONFIGURATION.model_copy(
    update={
        "policy_evaluation": SHIPPED_CONFIGURATION.policy_evaluation.model_copy(
            update={"enabled": True, "disabled_reason": None}
        )
    }
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Repository:
    """The case aggregate in memory, with `CaseRepository`'s semantics kept.

    Four of those semantics are load-bearing here and each one is reproduced
    rather than approximated, because the code under test is written against
    them:

    * **Optimistic versions.** `update_case`, `update_return_record` and
      `update_return_item` compare and set, and the loser raises
      `ConcurrencyConflictError`. The activity's one retry is only meaningful
      against a store that can actually refuse it.
    * **The unique `factId`.** The log is insert-only, so a second append under
      an id already present raises `DuplicateKeyError` -- which is what makes a
      replayed fact a no-op and what makes a *new* observation need a new id.
    * **The unique partial `(caseId, returnReference)`.** One RMA cannot be
      recorded twice against one case, and a record with no RMA yet is not
      indexed at all.
    * **The plan sect. 6.5 revision bump.** Every child write moves
      `cases.version` and `cases.updatedAt`. The invariant is the whole reason a
      revision assertion means anything.
    """

    def __init__(self) -> None:
        self.case: dict[str, Any] = {
            "caseId": CASE_ID,
            "tenantId": "tenant-a",
            "principalId": "associate-1",
            "status": ReturnCaseStatus.AWAITING_SUPPORT.value,
            "channelBWorkItemId": None,
            "orderReference": "CW273354",
            "version": 0,
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        self.facts: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.statuses: list[str] = []
        #: Set by a test to make the next `update_return_record` lose its race.
        self.conflict_once = False

    # --- the case -------------------------------------------------------

    def _touch(self) -> None:
        self.case["version"] = int(self.case["version"]) + 1
        self.case["updatedAt"] = NOW

    @property
    def revision(self) -> int:
        return int(self.case["version"])

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.case if case_id == CASE_ID else None

    async def update_case(
        self, case_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        del case_id
        if expected_version != self.case["version"]:
            raise ConcurrencyConflictError(CASE_ID)
        self.case.update(updates)
        self._touch()
        if "status" in updates:
            self.statuses.append(str(updates["status"]))
        return self.case

    # --- facts ----------------------------------------------------------

    async def append_case_fact(self, **fact: Any) -> dict[str, Any]:
        if any(existing["factId"] == fact["fact_id"] for existing in self.facts):
            raise DuplicateKeyError("factId")
        document = {
            "factId": fact["fact_id"],
            "caseId": fact["case_id"],
            "factName": fact["fact_name"],
            "value": fact["value"],
            "agentId": fact.get("agent_id"),
            "channel": fact["channel"].value,
            "acquisitionMethod": fact["acquisition_method"].value,
            "sourceSystem": fact.get("source_system"),
            "sourcePath": fact.get("source_path"),
            "supersedesFactId": fact.get("supersedes_fact_id"),
            "observedAt": fact.get("observed_at") or NOW,
            "recordedAt": NOW + timedelta(seconds=len(self.facts)),
        }
        self.facts.append(document)
        self._touch()
        return document

    def seed_fact(self, name: str, value: Any) -> None:
        """A fact placed by something other than this activity -- the policy
        evaluation, typically. Seeded rather than written through the activity
        because the gate is not what these tests are about."""
        self.facts.append(
            {
                "factId": f"seed-{name}-{len(self.facts)}",
                "caseId": CASE_ID,
                "factName": name,
                "value": value,
                "agentId": "seed",
                "channel": "SYSTEM",
                "acquisitionMethod": "DERIVED",
                "sourceSystem": None,
                "sourcePath": None,
                "supersedesFactId": None,
                "observedAt": NOW - timedelta(hours=1),
                "recordedAt": NOW - timedelta(hours=1),
            }
        )

    async def list_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts if fact["caseId"] == case_id]

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for document in await self.list_case_facts(case_id):
            name = str(document["factName"])
            current = latest.get(name)
            if current is None or (document["recordedAt"], str(document["factId"])) >= (
                current["recordedAt"],
                str(current["factId"]),
            ):
                latest[name] = document
        return latest

    def facts_named(self, name: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts if fact["factName"] == name]

    # --- return records --------------------------------------------------

    async def create_return_record(
        self,
        *,
        return_record_id: str,
        case_id: str,
        return_reference: str | None = None,
        status: str = "DRAFT",
        source_system: str | None = None,
    ) -> dict[str, Any]:
        if return_reference is not None and any(
            record["caseId"] == case_id and record["returnReference"] == return_reference
            for record in self.records
        ):
            raise DuplicateKeyError("caseId_returnReference")
        document = {
            "returnRecordId": return_record_id,
            "caseId": case_id,
            "returnReference": return_reference,
            "status": status,
            "returnLocation": None,
            "trackingReference": None,
            "labelReference": None,
            "shippingInstructionReference": None,
            "sourceSystem": source_system,
            "version": 0,
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        self.records.append(document)
        self._touch()
        return document

    async def list_return_records(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(record) for record in self.records if record["caseId"] == case_id]

    async def update_return_record(
        self, return_record_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        stored = next(
            (record for record in self.records if record["returnRecordId"] == return_record_id),
            None,
        )
        if stored is None:
            raise KeyError(return_record_id)
        if self.conflict_once:
            # A concurrent writer got there first. The record moves under this
            # caller's feet, exactly as it would in Mongo.
            self.conflict_once = False
            stored["version"] = int(stored["version"]) + 1
            self._touch()
            raise ConcurrencyConflictError(return_record_id)
        if expected_version != stored["version"]:
            raise ConcurrencyConflictError(return_record_id)
        stored.update(updates)
        stored["version"] = int(stored["version"]) + 1
        stored["updatedAt"] = NOW
        self._touch()
        return dict(stored)

    # --- items -----------------------------------------------------------

    async def list_case_return_items(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.items if item["caseId"] == case_id]

    async def assign_return_item_to_record(
        self, return_item_id: str, *, return_record_id: str, expected_version: int
    ) -> dict[str, Any]:
        stored = next(item for item in self.items if item["returnItemId"] == return_item_id)
        if expected_version != stored["version"]:
            raise ConcurrencyConflictError(return_item_id)
        stored["returnRecordId"] = return_record_id
        stored["version"] = int(stored["version"]) + 1
        self._touch()
        return dict(stored)

    # --- the read path ----------------------------------------------------

    async def load_case_projection_state(self, case_id: str) -> Any:
        """The shipped assembler over the shipped documents.

        The case is read first, exactly as `CaseRepository` reads it, so a
        reported revision can be stale-old and never stale-new.
        """
        if case_id != CASE_ID:
            return None
        return assemble_case_projection_state(
            CaseAggregateDocuments(
                case=dict(self.case),
                facts=await self.latest_case_facts(case_id),
                return_records=tuple(dict(record) for record in self.records),
                return_items=tuple(dict(item) for item in self.items),
                support_work_item=None,
            )
        )

    async def projection(self) -> CaseProjection:
        state = await self.load_case_projection_state(CASE_ID)
        assert state is not None
        return project_case(state, requirements=RELEASED_REQUIREMENTS)


class _ReturnStore:
    """The authoritative SQL return store, as T-14 needs it here."""

    def __init__(self) -> None:
        self.writes: list[Any] = []

    async def persist_case_return_records(self, write: Any) -> tuple[str, ...]:
        self.writes.append(write)
        return tuple(record.return_record_id for record in write.records)

    def last_record(self, reference: str) -> Any:
        for write in reversed(self.writes):
            for record in write.records:
                if record.return_reference == reference:
                    return record
        raise AssertionError(f"the return store never saw {reference}")


class _GraphSync:
    def __init__(self) -> None:
        self.synchronized: list[tuple[str, ...]] = []
        self.should_fail = False

    async def synchronize_records(
        self, *, case_id: str, return_record_ids: tuple[str, ...]
    ) -> ReturnRecordSyncOutcome:
        del case_id
        if self.should_fail:
            raise RuntimeError("the graph is unavailable")
        self.synchronized.append(return_record_ids)
        return ReturnRecordSyncOutcome(
            graph_generation_id="gen-1",
            synchronized_record_ids=return_record_ids,
            nodes_written=len(return_record_ids),
        )


class _SupportService:
    def __init__(self) -> None:
        self.work_items: dict[str, dict[str, Any]] = {}
        self.reminders: list[str] = []

    async def open_case_thread(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        queue: str | None = None,
    ) -> str:
        existing = next(
            (item for item in self.work_items.values() if item["caseId"] == case_id), None
        )
        if existing is not None:
            return str(existing["id"])
        work_item_id = f"wi-{len(self.work_items) + 1}"
        self.work_items[work_item_id] = {
            "id": work_item_id,
            "caseId": case_id,
            "tenantId": tenant_id,
            "principalId": principal_id,
            "queue": queue or "RETURNS_SUPPORT",
            "draft": support_draft,
            "idempotencyKey": idempotency_key,
        }
        return work_item_id

    async def post_reminder(self, **kwargs: Any) -> None:
        self.reminders.append(str(kwargs.get("idempotency_key")))


@dataclass
class _Fixture:
    repository: _Repository
    return_store: _ReturnStore
    graph_sync: _GraphSync
    support: _SupportService
    activities: ReturnCaseActivities
    minted: int = 0

    def next_record_id(self) -> str:
        self.minted += 1
        return f"minted-{self.minted}"


def _fixture(
    *,
    approved: bool = True,
    route: str = "STANDARD_RETURN",
    configuration: Any = SHIPPED_CONFIGURATION,
) -> _Fixture:
    repository = _Repository()
    if approved:
        # The completion profile needs an authority that stands. Seeded as the
        # policy facts the gate writes, not as a flag, so the shipped projection
        # derives it -- and for the two verification routes that means seeding
        # *only* the route, because `_record_policy_outcome` writes no decision
        # for them and `PolicyEvaluationProjection` refuses one.
        repository.seed_fact("policy_route", route)
        if route == "STANDARD_RETURN":
            repository.seed_fact("policy_decision", "APPROVE")
            repository.seed_fact("policy_effective_decision", "APPROVE")
    return_store = _ReturnStore()
    graph_sync = _GraphSync()
    support = _SupportService()
    return _Fixture(
        repository=repository,
        return_store=return_store,
        graph_sync=graph_sync,
        support=support,
        activities=ReturnCaseActivities(
            repository=cast(Any, repository),
            support_service=support,
            graph_sync=graph_sync,
            return_store=return_store,
            configuration=lambda: configuration,
        ),
    )


async def _deliver(
    fixture: _Fixture,
    *records: SupportReturnRecord,
    event_id: str,
    rejected: bool = False,
    reason: str | None = None,
) -> SupportOutcomeReceipt:
    """One Support notice, through the shipped activity."""
    return await fixture.activities.record_support_outcome(
        RecordSupportOutcomeInput(
            case_id=CASE_ID,
            work_item_id="wi-1",
            records=records,
            rejected=rejected,
            reason=reason,
            return_record_ids=tuple(fixture.next_record_id() for _ in records),
            support_event_id=event_id,
        )
    )


# ---------------------------------------------------------------------------
# Task 1 + 2: the notices accumulate and upsert by business identity
# ---------------------------------------------------------------------------


async def test_rma_then_tracking_then_label_then_pickup_all_reach_one_record() -> None:
    """The scenario the phase exists for, in the order it happens.

    Four notices about one RMA. Each carries only what Support knew at the
    time, and each has to land on the *same* record -- the upsert key is
    `(caseId, returnReference)`, so `RMA-1` is `RMA-1` however many times it is
    mentioned and whatever id the workflow minted for the attempt.

    "Pickup" here is the shipping instruction reference, because that is the
    field the case aggregate has for it: `PickupProjection` is keyed by session
    and no pickup reaches a Copilot case. The semantics are what plan sect. 10.1
    asks for -- an independently expressible `PICKUP_SCHEDULED` update -- and the
    enum is explicitly not.
    """
    fixture = _fixture()
    repository = fixture.repository

    revisions = [repository.revision]
    await _deliver(fixture, SupportReturnRecord(return_reference="RMA-1"), event_id="evt-rma")
    revisions.append(repository.revision)

    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-TRACK"),
        event_id="evt-tracking",
    )
    revisions.append(repository.revision)

    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", label_reference="LBL-1"),
        event_id="evt-label",
    )
    revisions.append(repository.revision)

    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", shipping_instruction_reference="PICKUP-9"),
        event_id="evt-pickup",
    )
    revisions.append(repository.revision)

    # One record, not four. The duplicate-key swallow is what used to make the
    # second reply a no-op; the upsert is what makes it an update.
    assert len(repository.records) == 1, "a later reply issued a second RMA"
    (record,) = repository.records
    assert record["returnReference"] == "RMA-1"
    assert record["trackingReference"] == "1Z-TRACK"
    assert record["labelReference"] == "LBL-1"
    assert record["shippingInstructionReference"] == "PICKUP-9"

    # The revision advances at every step. A client polling on `revision` sees
    # each arrival; one that did not move is one no screen would re-render for.
    assert revisions == sorted(revisions)
    assert all(later > earlier for earlier, later in pairwise(revisions)), (
        f"a Support notice landed without moving the revision: {revisions}"
    )

    # And the authoritative row carries the accumulated truth, not the last
    # notice's sparse view of it.
    stored = fixture.return_store.last_record("RMA-1")
    assert stored.tracking_reference == "1Z-TRACK"
    assert stored.label_reference == "LBL-1"
    assert stored.shipping_instruction_reference == "PICKUP-9"


async def test_the_reverse_order_converges_on_the_same_record() -> None:
    """RMA, then label, then tracking. Arrival order is not part of the answer.

    Support is a human on a queue and the transport below is at-least-once;
    neither guarantees an order, so a case that converged only for one sequence
    would be a case whose completeness depended on how busy the queue was.
    """
    fixture = _fixture()
    await _deliver(fixture, SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", label_reference="LBL-1"),
        event_id="evt-2",
    )
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-TRACK"),
        event_id="evt-3",
    )

    (record,) = fixture.repository.records
    assert record["labelReference"] == "LBL-1"
    assert record["trackingReference"] == "1Z-TRACK"


async def test_a_replayed_support_event_changes_no_record_and_bumps_no_revision() -> None:
    """The property that keeps at-least-once delivery from double-applying.

    The same notice under the same `supportEventId`, twice. Nothing about the
    record changes, and -- the part that matters to a client -- the revision does
    not move. A bump over an unchanged projection makes every poller re-fetch a
    case nothing happened to.
    """
    fixture = _fixture()
    notice = SupportReturnRecord(
        return_reference="RMA-1", tracking_reference="1Z-TRACK", label_reference="LBL-1"
    )
    await _deliver(fixture, notice, event_id="evt-1")

    before = fixture.repository.revision
    snapshot = dict(fixture.repository.records[0])
    facts_before = len(fixture.repository.facts)

    receipt = await _deliver(fixture, notice, event_id="evt-1")

    assert receipt.applied is False, "a redelivery reported itself as a write"
    assert fixture.repository.revision == before, "a redelivery moved the case revision"
    assert fixture.repository.records[0] == snapshot
    assert len(fixture.repository.facts) == facts_before, "a redelivery appended a fact"
    assert len(fixture.repository.records) == 1


async def test_a_null_field_in_a_later_update_does_not_erase_a_present_value() -> None:
    """Silence is not a deletion.

    The second notice carries a tracking number and `label_reference=None` --
    Support saying nothing about the label, which is what every partial reply
    does. Applying that null would delete a label the customer has already
    printed. Asserted in **both** stores, because the SQL upsert is a
    whole-row `SET` and would erase the column just as readily.
    """
    fixture = _fixture()
    await _deliver(
        fixture,
        SupportReturnRecord(
            return_reference="RMA-1", label_reference="LBL-1", return_location="DC-7"
        ),
        event_id="evt-1",
    )
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-TRACK"),
        event_id="evt-2",
    )

    (record,) = fixture.repository.records
    assert record["labelReference"] == "LBL-1", "a null erased the label"
    assert record["returnLocation"] == "DC-7", "a null erased the return location"
    assert record["trackingReference"] == "1Z-TRACK"

    stored = fixture.return_store.last_record("RMA-1")
    assert stored.label_reference == "LBL-1", "a null erased the label in the return store"
    assert stored.return_location == "DC-7"


async def test_a_corrected_value_supersedes_and_is_recorded_as_its_own_observation() -> None:
    """`TRACKING_UPDATED` and `LABEL_REPLACED`, which are corrections, not repeats.

    The fact log is insert-only against a unique `factId`. A second tracking
    number written under the first one's id would be absorbed as a duplicate and
    lost -- the log would say what Support first said and never what it
    corrected. The Support event id in the fact id is what keeps the two apart.
    """
    fixture = _fixture()
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-FIRST"),
        event_id="evt-1",
    )
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-CORRECTED"),
        event_id="evt-2",
    )

    (record,) = fixture.repository.records
    assert record["trackingReference"] == "1Z-CORRECTED"
    tracked = fixture.repository.facts_named("tracking_reference")
    assert [fact["value"] for fact in tracked] == ["1Z-FIRST", "1Z-CORRECTED"]
    latest = await fixture.repository.latest_case_facts(CASE_ID)
    assert latest["tracking_reference"]["value"] == "1Z-CORRECTED"


async def test_a_second_reply_carrying_a_new_rma_adds_a_record_rather_than_replacing_one() -> None:
    """One case, two RMAs. The multi-RMA shape the case model exists for."""
    fixture = _fixture()
    await _deliver(fixture, SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")
    await _deliver(fixture, SupportReturnRecord(return_reference="RMA-2"), event_id="evt-2")

    assert {record["returnReference"] for record in fixture.repository.records} == {
        "RMA-1",
        "RMA-2",
    }


async def test_a_losing_update_is_retried_once_against_the_record_it_lost_to() -> None:
    """The compare-and-set loser re-reads rather than re-sending.

    A concurrent writer moves the record between the read and the write. The
    activity is told `ConcurrencyConflictError`, re-reads under the business key,
    recomputes the merge and applies it at the version it now sees. Re-sending
    the same `$set` at a guessed version would be the lost update the optimistic
    check exists to catch.
    """
    fixture = _fixture()
    await _deliver(fixture, SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")

    fixture.repository.conflict_once = True
    receipt = await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z-TRACK"),
        event_id="evt-2",
    )

    assert receipt.applied is True
    (record,) = fixture.repository.records
    assert record["trackingReference"] == "1Z-TRACK", "the retry lost the update"


async def test_the_receipt_names_the_record_the_update_landed_on() -> None:
    """The graph sync follows the receipt, not the minted ids.

    A second reply updates the record the case already holds. Syncing the id the
    workflow minted for that attempt would point a targeted read at a document
    nothing was ever written to, and the RMA would be in the store and absent
    from the graph -- the exact state W2.5 exists to prevent.
    """
    fixture = _fixture()
    first = await _deliver(fixture, SupportReturnRecord(return_reference="RMA-1"), event_id="e1")
    second = await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z"),
        event_id="e2",
    )

    assert first.record_ids == second.record_ids
    assert second.record_ids == (fixture.repository.records[0]["returnRecordId"],)


# ---------------------------------------------------------------------------
# Task 4: the return method, and completion that can actually resolve
# ---------------------------------------------------------------------------


async def test_an_approved_prepaid_parcel_case_reaches_business_complete() -> None:
    """D23 closed, end to end, **through the writer**.

    Not by hand-placing a fact. The method arrives on the notice, the activity
    writes it, and the shipped projection then resolves the completion profile
    and finds nothing outstanding. Before this, `returnMethod` had no
    persistence anywhere in the case aggregate, so `awaiting` always held
    `RETURN_METHOD` and `businessComplete` could never become true for any
    Copilot case.
    """
    fixture = _fixture()
    receipt = await _deliver(
        fixture,
        SupportReturnRecord(
            return_reference="RMA-1",
            return_method="PREPAID_PARCEL",
            label_reference="LBL-1",
            tracking_reference="1Z-TRACK",
        ),
        event_id="evt-1",
    )

    (record,) = fixture.repository.records
    assert record["returnMethod"] == "PREPAID_PARCEL", "the method was not written to the record"
    # And as a per-record fact, under the name `case_placement.py` already reads.
    method_facts = fixture.repository.facts_named("return_method")
    assert [fact["value"] for fact in method_facts] == ["PREPAID_PARCEL"]

    projection = await fixture.repository.projection()
    assert projection.awaiting == ()
    assert projection.businessComplete is True
    assert receipt.completion_known is True
    assert receipt.business_complete is True
    assert receipt.awaiting == ()


# ---------------------------------------------------------------------------
# D3: a verified warranty or delivery claim is the standard route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "verification"),
    [
        ("WARRANTY", AwaitingDimension.WARRANTY_VERIFICATION),
        ("DELIVERY_CLAIM", AwaitingDimension.DELIVERY_CLAIM_VERIFICATION),
    ],
)
async def test_a_verified_route_completes_exactly_as_the_standard_route(
    route: str, verification: AwaitingDimension
) -> None:
    """D3, through the writer, as the A/B a live run measured it by.

    A live end-to-end against real Ferguson orders produced three cases with
    *identical* artifact completeness -- RMA, label, tracking -- and opposite
    outcomes: the standard route completed and both verification routes read
    `awaiting: [WARRANTY_VERIFICATION]` / `[DELIVERY_CLAIM_VERIFICATION]` with
    `businessComplete: false`, permanently. The dimension was raised on the
    route and cleared by nothing, and since these routes carry `decision: null`
    by construction their completion profile could never resolve either. A
    warranty case was therefore not a hand-off but a dead end -- the exact
    outcome that keeping them out of `ReturnCaseStatus` was meant to prevent.

    Both halves are asserted here: before Support answers the case waits on its
    verification, and afterwards it is indistinguishable from the standard
    route. What clears it is the authorization Support issued, which on these
    routes only Support's own reply can produce.
    """
    fixture = _fixture(route=route)

    before = await fixture.repository.projection()
    assert before.awaiting == (verification,)
    assert before.businessComplete is False

    receipt = await _deliver(
        fixture,
        SupportReturnRecord(
            return_reference="RMA-1",
            return_method="PREPAID_PARCEL",
            label_reference="LBL-1",
            tracking_reference="1Z-TRACK",
        ),
        event_id="evt-1",
    )

    projection = await fixture.repository.projection()
    assert projection.policyEvaluation is not None
    assert projection.policyEvaluation.effectiveDecision is None, (
        "a verification route carries no decision; the fix must not have invented one"
    )
    assert projection.awaiting == ()
    assert projection.businessComplete is True
    assert receipt.completion_known is True
    assert receipt.business_complete is True
    assert receipt.awaiting == ()

    # The A/B itself: same artifacts, same answer as the ordinary path.
    standard = _fixture()
    standard_receipt = await _deliver(
        standard,
        SupportReturnRecord(
            return_reference="RMA-1",
            return_method="PREPAID_PARCEL",
            label_reference="LBL-1",
            tracking_reference="1Z-TRACK",
        ),
        event_id="evt-1",
    )
    assert (receipt.business_complete, receipt.awaiting) == (
        standard_receipt.business_complete,
        standard_receipt.awaiting,
    )


@pytest.mark.parametrize("route", ["WARRANTY", "DELIVERY_CLAIM"])
async def test_a_verified_route_with_a_partial_rma_owes_what_the_table_says(route: str) -> None:
    """Rejoining the lifecycle means rejoining the requirement table, not skipping it.

    The verification is cleared and the paperwork is not. A fix that cleared the
    dimension by weakening completion would report this case done.
    """
    fixture = _fixture(route=route)
    receipt = await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-1",
    )

    assert receipt.business_complete is False
    assert set(receipt.awaiting) == {"LABEL", "TRACKING"}
    projection = await fixture.repository.projection()
    assert set(projection.awaiting) == {AwaitingDimension.LABEL, AwaitingDimension.TRACKING}


# ---------------------------------------------------------------------------
# The rejection half of the same hand-off: Support looked, and said no.
#
# D3 landed the verified side -- an RMA on a routed case is the recorded
# verification -- and left the refusal with no representation at all. The
# workflow read `rejected` to pick a status and this activity read neither
# `rejected` nor `reason`, so a refused claim left exactly one trace:
# `CaseStatus.CLOSED`, the same value a *finished* return reaches. The
# projection therefore reported the refusal as `COMPLETED_EXTERNAL_SETTLEMENT`
# -- terminal, and asserting a credit settled outside the platform -- while
# `awaiting` still named the verification the refusal had just answered.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["WARRANTY", "DELIVERY_CLAIM", "STANDARD_RETURN"])
async def test_a_refusal_is_recorded_rather_than_left_to_be_inferred(route: str) -> None:
    """Every route, because `rejected` is a property of the reply and not of the route.

    A standard return Support declines closes the same way and had the same
    hole; it simply raised `RETURN_METHOD` instead of a verification dimension.
    """
    fixture = _fixture(route=route)

    await _deliver(
        fixture,
        event_id="evt-1",
        rejected=True,
        reason="Serial number outside the warranty term.",
    )

    facts = await fixture.repository.latest_case_facts(CASE_ID)
    assert facts["support_outcome"]["value"] == "REJECTED"
    assert facts["support_outcome_reason"]["value"] == "Serial number outside the warranty term."
    # Provenance says who *recorded* the answer. It does not claim to know who
    # reached it -- `SupportResponseNotice` carries no actor -- and inventing a
    # verifier is the fabrication this programme exists to prevent.
    assert facts["support_outcome"]["agentId"] == "return-support"
    assert facts["support_outcome"]["channel"] == "CHANNEL_B"
    assert facts["support_outcome"]["sourceSystem"] == "RETURN_SUPPORT"
    assert facts["support_outcome"]["recordedAt"] is not None


@pytest.mark.parametrize(
    ("route", "verification"),
    [
        ("WARRANTY", AwaitingDimension.WARRANTY_VERIFICATION),
        ("DELIVERY_CLAIM", AwaitingDimension.DELIVERY_CLAIM_VERIFICATION),
    ],
)
async def test_a_refused_claim_is_not_terminal_and_awaiting_at_the_same_time(
    route: str, verification: AwaitingDimension
) -> None:
    """The incoherence, reproduced on the shipped writer and then absent.

    The status the workflow writes next is `CLOSED`, so the projection is taken
    at that status -- which is the only place the two endings meet.
    """
    fixture = _fixture(route=route)
    assert (await fixture.repository.projection()).awaiting == (verification,)

    await _deliver(fixture, event_id="evt-1", rejected=True, reason="Not covered.")
    # What `_record_support_outcome` does next, and the reason the fact is
    # written by the activity rather than after it: the status write must never
    # land on a case with no answer beside it.
    await fixture.repository.update_case(
        CASE_ID,
        {"status": ReturnCaseStatus.CLOSED.value},
        expected_version=fixture.repository.revision,
    )

    projection = await fixture.repository.projection()
    assert projection.status.value == "POLICY_REJECTED"
    assert projection.status.value != "COMPLETED_EXTERNAL_SETTLEMENT"
    assert projection.awaiting == ()
    assert projection.isTerminal is True
    assert projection.businessComplete is False


async def test_an_authorization_is_recorded_as_one_and_silence_as_neither() -> None:
    """`AUTHORIZED` is written only when Support actually issued something.

    A notice carrying neither a rejection nor a record states nothing about the
    case, and recording an outcome for it would be the projection asserting an
    answer nobody gave.
    """
    issued = _fixture()
    await _deliver(
        issued,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-1",
    )
    assert (await issued.repository.latest_case_facts(CASE_ID))["support_outcome"][
        "value"
    ] == "AUTHORIZED"

    silent = _fixture()
    await _deliver(silent, event_id="evt-1")
    assert "support_outcome" not in await silent.repository.latest_case_facts(CASE_ID)


async def test_a_redelivered_refusal_records_nothing_a_second_time() -> None:
    """The fact id carries the Support event, so a replay is absorbed.

    Insert-only against a unique `factId`, exactly as the RMA facts are: a
    redelivery must not manufacture a revision change on a projection nothing
    moved.
    """
    fixture = _fixture(route="WARRANTY")
    await _deliver(fixture, event_id="evt-1", rejected=True, reason="Not covered.")
    revision = fixture.repository.revision

    await _deliver(fixture, event_id="evt-1", rejected=True, reason="Not covered.")

    assert fixture.repository.revision == revision
    assert [fact["factName"] for fact in fixture.repository.facts].count("support_outcome") == 1


async def test_a_refusal_with_no_reason_records_the_outcome_and_no_empty_reason() -> None:
    """Absence is data. A blank reason recorded beside the outcome would read as
    a reason that says nothing, which hides the absence rather than reporting
    it."""
    fixture = _fixture(route="WARRANTY")
    await _deliver(fixture, event_id="evt-1", rejected=True, reason="   ")

    facts = await fixture.repository.latest_case_facts(CASE_ID)
    assert facts["support_outcome"]["value"] == "REJECTED"
    assert "support_outcome_reason" not in facts


async def test_the_method_arriving_after_the_rma_is_what_completes_the_case() -> None:
    """The delayed half of D23. A partial RMA waits; the method resolves it."""
    fixture = _fixture()
    await _deliver(
        fixture,
        SupportReturnRecord(
            return_reference="RMA-1", label_reference="LBL-1", tracking_reference="1Z"
        ),
        event_id="evt-1",
    )
    before = await fixture.repository.projection()
    assert before.businessComplete is False
    assert AwaitingDimension.RETURN_METHOD in before.awaiting

    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-2",
    )

    after = await fixture.repository.projection()
    assert after.awaiting == ()
    assert after.businessComplete is True


async def test_a_partial_rma_leaves_the_case_incomplete() -> None:
    """An RMA and a method and nothing else. The label and the number are owed.

    The requirement table decides, not a hardcoded rule: `PREPAID_PARCEL`
    requires `RMA`, `LABEL` and `TRACKING`, and two of those are outstanding.
    """
    fixture = _fixture()
    receipt = await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-1",
    )

    assert receipt.completion_known is True
    assert receipt.business_complete is False
    projection = await fixture.repository.projection()
    assert set(projection.awaiting) == {AwaitingDimension.LABEL, AwaitingDimension.TRACKING}


async def test_two_rmas_with_different_methods_are_evaluated_against_their_own_rows() -> None:
    """The reason the method is per record and never per case.

    A `CUSTOMER_KEEP` record needs only its authorization and is done. A
    `PREPAID_PARCEL` record on the *same case* still owes a tracking number. A
    single case-level method would read as the method of both, silently
    completing one against the other's requirement set.
    """
    fixture = _fixture()
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-KEEP", return_method="CUSTOMER_KEEP"),
        SupportReturnRecord(
            return_reference="RMA-SHIP",
            return_method="PREPAID_PARCEL",
            label_reference="LBL-1",
        ),
        event_id="evt-1",
    )

    projection = await fixture.repository.projection()
    methods = {record.returnReference: record.returnMethod for record in projection.records()}
    assert methods == {"RMA-KEEP": "CUSTOMER_KEEP", "RMA-SHIP": "PREPAID_PARCEL"}
    # The parcel still owes its tracking number; the keep owes nothing.
    assert AwaitingDimension.TRACKING in projection.awaiting
    assert projection.businessComplete is False


async def test_a_method_arriving_null_does_not_erase_the_one_already_recorded() -> None:
    fixture = _fixture()
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-1",
    )
    await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z"),
        event_id="evt-2",
    )

    (record,) = fixture.repository.records
    assert record["returnMethod"] == "PREPAID_PARCEL"


# ---------------------------------------------------------------------------
# The completion the workflow acts on is the one the API answers
# ---------------------------------------------------------------------------


def _release_where_a_parcel_needs_only_its_rma() -> Any:
    """The shipped release with one row edited, as an operator could edit it.

    `PREPAID_PARCEL` keeps only `RMA` -- a deployment that does not paper its
    own parcels, say. That single change is what makes the release and
    `DEFAULT_RETURN_METHOD_REQUIREMENTS` disagree about a case holding nothing
    but an authorization: the constant says it is still waiting for a label and
    a tracking number, the release says it is finished.

    Round-tripped through `model_validate` rather than constructed field by
    field, so the altered table is a release that would actually load --
    including `validate_return_method_requirements`, which is what a real
    operator edit has to survive.
    """
    payload = SHIPPED_CONFIGURATION.model_dump(mode="json")
    payload["return_policy"]["return_method_requirements"] = [
        ({**row, "requires": ["RMA"]} if row["method"] == "PREPAID_PARCEL" else row)
        for row in payload["return_policy"]["return_method_requirements"]
    ]
    return ReturnPlatformConfiguration.model_validate(payload)


async def _an_authorized_parcel(fixture: _Fixture) -> SupportOutcomeReceipt:
    """One `PREPAID_PARCEL` RMA and nothing else -- no label, no tracking."""
    return await _deliver(
        fixture,
        SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
        event_id="evt-1",
    )


async def test_the_workflow_reads_completion_from_the_release_not_the_code_constant() -> None:
    """The divergence this closed, made visible by the edit that would cause it.

    `_assess_completion` called `project_case(state)` with no table, so the
    decision the run loop acts on came from `DEFAULT_RETURN_METHOD_REQUIREMENTS`
    while `GET /api/cases/{caseId}` answered from
    `return_policy.return_method_requirements`. The shipped rows are identical
    to the constant's, so nothing had diverged -- which is exactly why this test
    edits one row: on the released table below the case is complete, on the
    constant it is still waiting for `LABEL` and `TRACKING`, and the receipt
    says complete.

    The assertion is stated in both directions on purpose. "Complete" alone
    would also pass if the activity had stopped assessing anything.
    """
    fixture = _fixture(configuration=_release_where_a_parcel_needs_only_its_rma())

    receipt = await _an_authorized_parcel(fixture)

    assert receipt.completion_known is True
    assert receipt.business_complete is True
    assert receipt.awaiting == ()

    # And the constant, on the same case, would have said the opposite.
    state = await fixture.repository.load_case_projection_state(CASE_ID)
    assert state is not None
    by_constant = project_case(state, requirements=DEFAULT_RETURN_METHOD_REQUIREMENTS)
    assert by_constant.businessComplete is False
    assert set(by_constant.awaiting) == {AwaitingDimension.LABEL, AwaitingDimension.TRACKING}


async def test_the_workflow_and_the_api_answer_the_same_case_identically() -> None:
    """One release, two readers, one answer -- through both real code paths.

    The workflow's table comes from `ReturnCaseActivities._requirement_table`
    and the API's from `api/cases.py::_requirement_table`, and neither is
    imitated here: the activity is the shipped one and the request below is fed
    to the shipped dependency. Under the edited release the two now agree that
    the case is complete; before the fix the API said complete and the workflow
    said `awaiting: [LABEL, TRACKING]` about the same case at the same instant.
    """
    configuration = _release_where_a_parcel_needs_only_its_rma()
    fixture = _fixture(configuration=configuration)

    receipt = await _an_authorized_parcel(fixture)

    request = cast(
        Any,
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    return_configuration=LoadedReturnConfiguration(
                        configuration=configuration,
                        path=DEFAULT_RETURN_CONFIGURATION_PATH,
                        sha256="0" * 64,
                    )
                )
            )
        ),
    )
    state = await fixture.repository.load_case_projection_state(CASE_ID)
    assert state is not None
    answered = project_case(state, requirements=api_requirement_table(request))

    assert (receipt.business_complete, receipt.awaiting) == (
        answered.businessComplete,
        tuple(str(dimension) for dimension in answered.awaiting),
    )
    assert receipt.revision == answered.revision


async def test_a_worker_with_no_configuration_source_reports_completion_unknown() -> None:
    """The activity double's answer, unchanged.

    An activity set constructed without the `configuration` callable cannot
    resolve a table and never will -- it is a property of the wiring, identical
    on every retry, and the same class of fact as a repository with no
    `load_case_projection_state`. It answers "we cannot tell", which is what
    every pre-Phase-4 double answered and what the run loop is written for.
    """
    fixture = _fixture()
    activities = ReturnCaseActivities(
        repository=cast(Any, fixture.repository),
        support_service=fixture.support,
        graph_sync=fixture.graph_sync,
        return_store=fixture.return_store,
    )

    receipt = await activities.record_support_outcome(
        RecordSupportOutcomeInput(
            case_id=CASE_ID,
            work_item_id="wi-1",
            records=(
                SupportReturnRecord(
                    return_reference="RMA-1",
                    return_method="PREPAID_PARCEL",
                    label_reference="LBL-1",
                    tracking_reference="1Z-TRACK",
                ),
            ),
            rejected=False,
            reason=None,
            return_record_ids=("minted-1",),
            support_event_id="evt-1",
        )
    )

    assert receipt.completion_known is False
    assert receipt.business_complete is False
    assert receipt.awaiting == ()
    # The outcome itself was still recorded. "We cannot tell whether it is
    # finished" is not "we did not write it".
    assert [record["returnReference"] for record in fixture.repository.records] == ["RMA-1"]


async def test_an_unavailable_release_fails_the_attempt_rather_than_assuming_one() -> None:
    """A wired worker with no active release raises, and says why.

    The other two answers were both worse. Substituting the constant is the
    defect this change closed. Reporting "we cannot tell" would be worse still
    here: the run loop *ends* a drained case whose completion is unknown, so a
    configuration gap of a few seconds at startup would permanently stop
    supervising every case Support answered during it -- silently, behind a
    warning log. Failing the attempt is what `_PERSIST_RETRY` exists for, and it
    is the activity's version of the `503 ... "retryable": true` the read path
    answers in the same situation.
    """
    fixture = _fixture(configuration=None)

    with pytest.raises(RuntimeError, match="no return configuration is active"):
        await _an_authorized_parcel(fixture)

    # Failing the attempt costs nothing, and that is what makes it the right
    # answer rather than merely the loud one: the writes that precede the
    # completion read are idempotent, so the retry Temporal schedules lands on
    # the same RMA and assesses it once a release is active.
    assert [record["returnReference"] for record in fixture.repository.records] == ["RMA-1"]

    retried = ReturnCaseActivities(
        repository=cast(Any, fixture.repository),
        support_service=fixture.support,
        graph_sync=fixture.graph_sync,
        return_store=fixture.return_store,
        configuration=lambda: SHIPPED_CONFIGURATION,
    )
    receipt = await retried.record_support_outcome(
        RecordSupportOutcomeInput(
            case_id=CASE_ID,
            work_item_id="wi-1",
            records=(
                SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
            ),
            rejected=False,
            reason=None,
            return_record_ids=(fixture.next_record_id(),),
            support_event_id="evt-1",
        )
    )

    assert [record["returnReference"] for record in fixture.repository.records] == ["RMA-1"]
    assert receipt.completion_known is True
    assert receipt.business_complete is False
    assert set(receipt.awaiting) == {"LABEL", "TRACKING"}


# ---------------------------------------------------------------------------
# The workflow: alive until complete
# ---------------------------------------------------------------------------


class _ContinueAsNew(Exception):
    def __init__(self, workflow_input: ReturnCaseWorkflowInput) -> None:
        super().__init__("continue_as_new")
        self.workflow_input = workflow_input


class _Info:
    @staticmethod
    def is_continue_as_new_suggested() -> bool:
        return False


class _Runtime:
    """The `temporalio.workflow` functions the run loop calls.

    The same substitution `tests/policy/test_case_policy_gate.py` documents:
    `ReturnCaseWorkflow` is an ordinary class reaching the outside world through
    a handful of module functions, so replacing them drives the shipped run loop
    with the shipped activities behind it. Durability is not claimed here and is
    proved against a real server in `tests/test_return_case_workflow_real_infra.py`.
    """

    def __init__(
        self,
        activities: dict[str, Callable[[Any], Awaitable[Any]]],
        *,
        arrivals: list[Callable[[], None]] | None = None,
        patches: bool | Mapping[str, bool] = True,
    ) -> None:
        self._activities = activities
        self._arrivals = list(arrivals or [])
        self._uuid = 0
        self._patches = patches
        self.calls: list[str] = []
        self.patch_ids: list[str] = []
        self.options: list[tuple[str, dict[str, Any]]] = []
        self.instant = NOW
        self.logger = logging.getLogger("tests.phase4")

    async def execute_activity(self, name: str, argument: Any, **options: Any) -> Any:
        """Run the activity, and wrap a raised one the way Temporal would.

        The wrapping matters. The workflow's failure handling is written against
        `ActivityError` -- a graph sync that raised a bare `RuntimeError` here
        would escape the `except ActivityError` that parks the case, and the
        test would prove the opposite of what it claims.

        `self.options` records the keyword options alongside the name. The two
        limbs of `_PATCH_STRUCTURED_SUPPORT_DRAFT` call the *same* activity with
        the same input and differ only in whether `result_type` is pinned, so
        the name alone cannot tell them apart -- and a test that cannot tell
        them apart is not covering either.
        """
        self.calls.append(name)
        self.options.append((name, dict(options)))
        try:
            return await self._activities[name](argument)
        except Exception as error:
            raise ActivityError(
                str(error),
                scheduled_event_id=0,
                started_event_id=0,
                identity="test",
                activity_type=name,
                activity_id=name,
                retry_state=None,
            ) from error

    def patched(self, patch_id: str) -> bool:
        """Answer the way the execution this runtime is standing in for would.

        `workflow.patched(id)` is not a feature flag and must not be doubled as
        one. On an execution with no history it writes the marker and returns
        `True`; replaying a history recorded *before* that marker exists, it
        returns `False`, because the only safe thing to do with such a history
        is to keep running the code that produced it. That asymmetry is the
        whole mechanism, so a double hardcoded to `True` would leave the legacy
        limb of every gate exactly as unreachable as having no `patched` at all
        -- it would only move the failure from an `AttributeError` to silence.

        So the answer is a constructor choice, not a constant. `patches=True`
        (the default) is the faithful answer for these tests, which all start
        from nothing and are therefore new executions; `patches=False` builds a
        runtime that answers as a history older than every marker, which is the
        only way the un-patched branch is reachable from a test at all.

        **A mapping, not just a flag, and the reason is in production's own
        comments.** `_PATCH_STRUCTURED_SUPPORT_DRAFT` documents a real
        population -- histories that ran after `eaed61c` and before the marker
        existed -- for which one marker is absent and the behaviour it guards is
        already present. Markers are written independently, so a history carries
        an arbitrary *subset* of them, and a single boolean cannot express the
        subsets. `{id: answer}` can, and it is also what lets one test hold the
        draft gate fixed while it moves the review gate, which is the only way
        either gate's limbs are separable at all.

        An id absent from a supplied mapping raises `KeyError` rather than
        defaulting. That is deliberate: if `_open_support` grows a fourth
        `workflow.patched` call, a test pinning the three it knows about must
        fail loudly rather than quietly answer the new one at random -- the same
        guarantee `_LegacyRuntime.patched` gets from its `assert`.

        `self.patch_ids` records which markers were consulted, in order. That is
        the part that must not drift silently: the count and the ids are how a
        test asserts the gate was *reached* rather than merely stepped over.

        The same shape as `tests/policy/test_case_policy_gate.py::_Runtime` and
        `tests/test_support_template_review_gate.py::_Runtime`, deliberately --
        three runtimes standing in for one module should not each invent their
        own idea of what a patch marker is.
        """
        self.patch_ids.append(patch_id)
        if isinstance(self._patches, bool):
            return self._patches
        return self._patches[patch_id]

    def now(self) -> datetime:
        return self.instant

    def uuid4(self) -> uuid.UUID:
        self._uuid += 1
        return uuid.UUID(int=self._uuid)

    async def wait_condition(
        self,
        predicate: Callable[[], bool],
        *,
        timeout: timedelta | None = None,  # noqa: ASYNC109 - mirrors workflow.wait_condition
        timeout_summary: str | None = None,
    ) -> None:
        del timeout_summary
        if self._arrivals:
            self._arrivals.pop(0)()
        if predicate():
            return
        self.instant += timeout if timeout is not None else timedelta(seconds=1)
        raise TimeoutError

    @staticmethod
    def info() -> _Info:
        return _Info()

    @staticmethod
    def all_handlers_finished() -> bool:
        return True

    @staticmethod
    def continue_as_new(workflow_input: ReturnCaseWorkflowInput) -> None:
        raise _ContinueAsNew(workflow_input)


def _timings(**overrides: Any) -> ReturnCaseTimings:
    base: dict[str, Any] = {
        "bay_wait_seconds": 0,
        "support_response_wait_seconds": 3600,
        "reminder_interval_seconds": 600,
        "max_reminders": 2,
        "on_reminders_exhausted": "PARK_FOR_OPERATIONS",
        "business_calendar_id": "default",
        "timezone": "UTC",
    }
    base.update(overrides)
    return ReturnCaseTimings(**base)


@dataclass
class _WorkflowHarness:
    instance: ReturnCaseWorkflow
    fixture: _Fixture
    runtime: _Runtime
    arrivals: list[Callable[[], None]] = field(default_factory=list)


def _harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    approved: bool = True,
    configuration: Any = SHIPPED_CONFIGURATION,
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] | None = None,
    patches: bool | Mapping[str, bool] = True,
) -> _WorkflowHarness:
    fixture = _fixture(approved=approved, configuration=configuration)
    instance = ReturnCaseWorkflow()
    activities = fixture.activities
    table: dict[str, Callable[[Any], Awaitable[Any]]] = {
        "record_case_status": activities.record_case_status,
        "resolve_business_deadline": activities.resolve_business_deadline,
        "request_bay_assignment": activities.request_bay_assignment,
        "evaluate_case_eligibility": activities.evaluate_case_eligibility,
        "draft_support_request": activities.draft_support_request,
        "open_support_work_item": activities.open_support_work_item,
        "send_support_reminder": activities.send_support_reminder,
        "record_support_outcome": activities.record_support_outcome,
        "synchronize_return_records": activities.synchronize_return_records,
    }
    runtime = _Runtime(
        table,
        arrivals=[
            (lambda step=step: step(instance))  # type: ignore[misc]
            for step in (arrivals or [])
        ],
        patches=patches,
    )
    monkeypatch.setattr(workflow_module, "workflow", runtime)
    return _WorkflowHarness(instance=instance, fixture=fixture, runtime=runtime)


async def _run(
    harness: _WorkflowHarness,
    *,
    timings: ReturnCaseTimings | None = None,
    work_item_id: str | None = "wi-1",
) -> ReturnCaseOutcome:
    """Run from the Support wait onwards.

    `resumed_work_item_id` is supplied so the run enters the drain directly.
    That is not a convenience: it is the `work_item_id is None` guard of 3A.7,
    and a case that already holds a work item must keep skipping the policy gate
    -- otherwise every `continue_as_new` re-evaluates a case Support is already
    working on. The gate itself is exercised separately below.
    """
    return await harness.instance.run(
        ReturnCaseWorkflowInput(
            case_id=CASE_ID,
            tenant_id="tenant-a",
            principal_id="associate-1",
            conversation_id="conv-1",
            configuration_release_id="release-1",
            timings=timings or _timings(),
            resumed_work_item_id=work_item_id,
            resumed_status=ReturnCaseStatus.AWAITING_SUPPORT.value,
        )
    )


def _notice(*records: SupportReturnRecord, event_id: str) -> SupportResponseNotice:
    return SupportResponseNotice(work_item_id="wi-1", records=records, support_event_id=event_id)


def _bare(monkeypatch: pytest.MonkeyPatch) -> ReturnCaseWorkflow:
    """A workflow instance whose handlers can be called directly.

    The runtime substitution is needed even with no activity in sight: the
    handlers log through `workflow.logger`, which outside a workflow context is
    the real Temporal module and refuses to be read.
    """
    monkeypatch.setattr(workflow_module, "workflow", _Runtime({}))
    return ReturnCaseWorkflow()


async def test_three_notices_on_one_workflow_and_the_case_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point, on one live execution.

    The RMA arrives, then the label, then the tracking number, each while the
    workflow is still waiting. Nothing is answered `500 workflow execution
    already completed`, no RMA is duplicated, and the case closes only when the
    requirement table says it is done.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
                event_id="evt-1",
            )
        ),
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(return_reference="RMA-1", label_reference="LBL-1"),
                event_id="evt-2",
            )
        ),
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(return_reference="RMA-1", tracking_reference="1Z"),
                event_id="evt-3",
            )
        ),
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(harness)

    assert outcome.support_responses_applied == 3, "a later notice never reached the case"
    assert outcome.business_complete is True
    assert outcome.awaiting == ()
    assert outcome.status == ReturnCaseStatus.CLOSED.value
    assert len(harness.fixture.repository.records) == 1, "the case grew a duplicate RMA"
    (record,) = harness.fixture.repository.records
    assert record["labelReference"] == "LBL-1"
    assert record["trackingReference"] == "1Z"
    # Every applied notice synchronized the record it landed on.
    assert harness.fixture.graph_sync.synchronized == [(record["returnRecordId"],)] * 3


async def test_a_partial_rma_leaves_the_workflow_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The RMA arrives and the case is not finished with it.

    What this replaces returned the moment it had recorded anything, which is
    why a delayed tracking number met a closed execution. Here the run goes on
    waiting -- and when nothing more arrives it *parks*, under a reason naming
    the deadline, rather than reporting a return complete.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
                event_id="evt-1",
            )
        )
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(harness)

    assert outcome.business_complete is False
    assert outcome.status != ReturnCaseStatus.CLOSED.value
    assert outcome.parked_reason == "SUPPORT_INCOMPLETE_AT_DEADLINE"
    projection = await harness.fixture.repository.projection()
    assert set(projection.awaiting) == {AwaitingDimension.LABEL, AwaitingDimension.TRACKING}


async def test_a_redelivered_notice_is_not_queued_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deduplicated on `supportEventId`, in the handler, before any activity runs."""
    instance = _bare(monkeypatch)
    notice = _notice(SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")

    instance.support_response(notice)
    instance.support_response(notice)

    assert len(instance._state.pending_support) == 1


async def test_two_distinct_events_both_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = _bare(monkeypatch)
    instance.support_response(_notice(SupportReturnRecord(return_reference="R1"), event_id="a"))
    instance.support_response(_notice(SupportReturnRecord(return_reference="R2"), event_id="b"))

    assert len(instance._state.pending_support) == 2


async def test_a_notice_with_no_event_id_keeps_first_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-`supportEventId` sender's guarantee, unchanged.

    An unkeyed notice cannot be told apart from a second one, so appending
    unconditionally would double-apply on every redelivery. First wins -- which
    is exactly what that sender got before this phase.
    """
    instance = _bare(monkeypatch)
    instance.support_response(
        SupportResponseNotice(
            work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
        )
    )
    instance.support_response(
        SupportResponseNotice(
            work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-2"),)
        )
    )

    assert len(instance._state.pending_support) == 1
    assert instance._state.pending_support[0].records[0].return_reference == "RMA-1"


async def test_the_remembered_event_ids_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An applied-keys set that only grows is state every history reset carries.

    The bound is what keeps redelivery protection from becoming a workflow that
    serializes an ever-larger list into every `continue_as_new`. The durable
    dedup is the unique `(caseId, supportEventId)` index and an activity that
    writes nothing when nothing changed; this set only has to cover commands
    still in flight.
    """
    instance = _bare(monkeypatch)
    for index in range(workflow_module._TRACKED_SUPPORT_EVENT_IDS + 20):
        instance.support_response(
            _notice(SupportReturnRecord(return_reference=f"R{index}"), event_id=f"evt-{index}")
        )

    assert len(instance._state.support_event_ids) == workflow_module._TRACKED_SUPPORT_EVENT_IDS
    # Newest kept, oldest dropped.
    assert instance._state.support_event_ids[-1] == (
        f"evt-{workflow_module._TRACKED_SUPPORT_EVENT_IDS + 19}"
    )


async def test_continue_as_new_carries_the_event_ids_and_the_lifetime_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history reset must not make a redelivery look new, or restart the cap."""
    instance = _bare(monkeypatch)
    instance._input = ReturnCaseWorkflowInput(
        case_id=CASE_ID,
        tenant_id="tenant-a",
        principal_id="associate-1",
        conversation_id="conv-1",
        configuration_release_id="release-1",
        timings=_timings(),
    )
    instance.support_response(_notice(SupportReturnRecord(return_reference="R1"), event_id="a"))
    instance._state.lifetime_start_iso = NOW.isoformat()
    instance._state.business_complete = False

    carried = instance._continued_input()

    assert carried.resumed_support_event_ids == ("a",)
    assert carried.resumed_lifetime_start_iso == NOW.isoformat()


async def test_the_absolute_lifetime_cap_parks_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A case may not stay open forever, however often its history resets.

    Parked rather than closed: the RMA it holds may yet be fulfilled, and a
    terminal marking would stop every client polling a return that is still
    real.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")
        )
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(
        harness,
        timings=_timings(
            support_response_wait_seconds=100_000,
            reminder_interval_seconds=600,
            max_reminders=0,
            absolute_lifetime_seconds=1,
        ),
    )

    assert outcome.parked_reason == "CASE_LIFETIME_CAP_REACHED"
    assert outcome.terminal_command == TerminalCommandName.EXPIRE.value


async def test_nothing_from_support_still_parks_on_the_reminder_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing behaviour, unchanged where it was right.

    With nothing received at all there is nothing to wait for, so the reminder
    cap still ends the wait and parks the case for an operator. The change is
    only that a case which *has* received something goes on collecting the rest.
    """
    harness = _harness(monkeypatch)

    outcome = await _run(harness, timings=_timings(max_reminders=2))

    assert outcome.reminders_sent == 2
    assert outcome.parked_reason == "SUPPORT_REMINDERS_EXHAUSTED"
    assert outcome.support_responses_applied == 0


# ---------------------------------------------------------------------------
# Terminal commands (plan sect. 10.3)
# ---------------------------------------------------------------------------


async def test_complete_is_refused_while_the_case_still_awaits_something(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COMPLETE` must satisfy domain completion; otherwise it is a 409.

    The refusal is recorded on the case rather than only logged -- a console
    told nothing would ask again, and an operator has to be able to see why the
    case did not close. And the case stays alive afterwards: a refused close is
    not a reason to stop collecting.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(return_reference="RMA-1", return_method="PREPAID_PARCEL"),
                event_id="evt-1",
            )
        ),
        lambda case: case.terminal_command(
            CaseTerminalCommand(
                command=TerminalCommandName.COMPLETE.value,
                reason_code="OPERATOR_JUDGEMENT",
                actor="supervisor-1",
            )
        ),
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(harness)

    assert outcome.status != ReturnCaseStatus.CLOSED.value
    assert outcome.terminal_command != TerminalCommandName.COMPLETE.value
    refusals = [
        fact["value"]
        for fact in harness.fixture.repository.facts_named("case_status")
        if str(fact["value"]).startswith("COMPLETE_REFUSED")
    ]
    assert refusals == ["COMPLETE_REFUSED:CASE_NOT_BUSINESS_COMPLETE"]


async def test_cancel_is_audited_with_a_server_derived_actor_and_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client supplies `reasonCode` and `reason`. Nothing else.

    `CaseTerminalCommand` has no timestamp field at all, so a caller cannot
    supply one; the workflow stamps `workflow.now()`. The actor is the
    authenticated principal the endpoint resolved, and the pair is written to
    the case fact log where an audit can find it.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.terminal_command(
            CaseTerminalCommand(
                command=TerminalCommandName.CANCEL.value,
                reason_code="CUSTOMER_WITHDREW",
                actor="supervisor-1",
                reason="The customer changed their mind.",
            )
        )
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(harness)

    assert outcome.status == ReturnCaseStatus.CANCELLED.value
    assert outcome.terminal_command == TerminalCommandName.CANCEL.value
    (audit,) = [
        str(fact["value"])
        for fact in harness.fixture.repository.facts_named("case_status")
        if str(fact["value"]).startswith("CANCEL:")
    ]
    assert audit == f"CANCEL:CUSTOMER_WITHDREW:supervisor-1:{NOW.isoformat()}"


async def test_a_signalled_expire_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`EXPIRE` is system-initiated. An actor who could assert one would have an
    unaudited cancellation wearing another name."""
    instance = _bare(monkeypatch)
    instance.terminal_command(
        CaseTerminalCommand(
            command=TerminalCommandName.EXPIRE.value,
            reason_code="DEADLINE",
            actor="supervisor-1",
        )
    )

    assert instance._state.terminal_command is None


async def test_a_command_that_is_not_one_of_the_three_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = _bare(monkeypatch)
    instance.terminal_command(
        CaseTerminalCommand(command="CLOSE", reason_code="ANY", actor="supervisor-1")
    )

    assert instance._state.terminal_command is None


async def test_a_cancel_with_no_actor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancellation nobody is attributed with is not audited."""
    instance = _bare(monkeypatch)
    instance.terminal_command(
        CaseTerminalCommand(
            command=TerminalCommandName.CANCEL.value, reason_code="ANY", actor="   "
        )
    )

    assert instance._state.terminal_command is None


async def test_complete_closes_a_case_that_does_satisfy_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the validation."""
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(
                SupportReturnRecord(
                    return_reference="RMA-1",
                    return_method="CUSTOMER_KEEP",
                ),
                event_id="evt-1",
            )
        )
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)

    outcome = await _run(harness)

    # `CUSTOMER_KEEP` requires only the authorization, so the case is complete
    # on the first notice and closes without any command at all.
    assert outcome.business_complete is True
    assert outcome.status == ReturnCaseStatus.CLOSED.value


# ---------------------------------------------------------------------------
# 3A.7 still holds
# ---------------------------------------------------------------------------


async def test_a_worker_that_cannot_report_completion_behaves_as_it_did_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility guarantee, asserted rather than assumed.

    A worker whose `record_support_outcome` answers `None` -- every activity
    double registered before this phase, and any repository that cannot assemble
    the projection -- cannot say whether the case is done. Holding it open on an
    unanswerable question would hang it, so recording the outcome is where it
    ends, which is exactly the pre-Phase-4 behaviour.

    The `result_type` the workflow passes is optional for the same reason: a
    bare dataclass hint raises `TypeError` inside Temporal's converter on a null
    payload, and that failure would land as a workflow task failure rather than
    as a value the run loop can read.
    """
    harness = _harness(
        monkeypatch,
        arrivals=[
            lambda case: case.support_response(
                _notice(SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")
            )
        ],
    )

    async def _silent(_request: Any) -> None:
        return None

    harness.runtime._activities["record_support_outcome"] = _silent

    outcome = await _run(harness)

    assert outcome.status == ReturnCaseStatus.RMA_RECEIVED.value
    assert outcome.business_complete is False
    assert outcome.parked_reason is None, "an unanswerable completion parked a case"
    assert workflow_module._RECEIPT_RESULT_TYPE == SupportOutcomeReceipt | None


async def test_a_rejected_return_still_opens_no_work_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3A.7's central claim, re-asserted against the rewritten run loop.

    The gate landed as a deliberate insertion, and its guarantee is positional:
    `if not await self._policy_cleared(timings): return ...` is the statement
    **immediately before** `await self._open_support(timings)`. Turning the
    Support wait into an accumulating drain is exactly the kind of change that
    could reopen that defect by giving the drain another way in, so the claim is
    checked here as well as in `tests/policy/test_case_policy_gate.py` -- and
    against the *work-item collection*, because a case can be marked
    `POLICY_REJECTED` and still have opened a thread with a human on the end of
    it.

    Run against `POLICY_ENABLED_CONFIGURATION`, because the claim is about what
    the gate *decides* and the shipped release currently suspends it. Under the
    shipped release this test passed its own assertions for the wrong reason --
    `SKIPPED_BY_CONFIGURATION` clears the gate, so nothing was rejected and "no
    work item" was true of a case that had simply not been judged.
    """
    harness = _harness(monkeypatch, configuration=POLICY_ENABLED_CONFIGURATION)
    for name in (
        "condition_new",
        "suitable_for_resale",
        "original_packaging",
        "packaging_undamaged",
        "all_original_parts",
        "seller_stocked",
    ):
        harness.fixture.repository.seed_fact(name, True)
    for name in (
        "installed",
        "modified",
        "rebuilt",
        "reconditioned",
        "repaired",
        "altered",
        "damaged",
        "special_order",
        "non_stock",
    ):
        harness.fixture.repository.seed_fact(name, False)
    harness.fixture.repository.seed_fact("return_reason", "CHANGED_MIND")
    harness.fixture.repository.seed_fact("purchase_date", "2026-08-01T10:00:00+00:00")
    # The one fact that decides it. The item was used; the Ferguson standard
    # return requires it not to be.
    harness.fixture.repository.seed_fact("used", True)
    for fact in harness.fixture.repository.facts:
        fact["acquisitionMethod"] = "STATED"
        fact["sourceSystem"] = "CONVERSATION"
        fact["sourcePath"] = "CONVERSATION_MESSAGE"

    outcome = await _run(harness, work_item_id=None)

    assert harness.fixture.support.work_items == {}, "a rejected return opened a work item"
    assert outcome.status == ReturnCaseStatus.POLICY_REJECTED.value
    assert outcome.work_item_id is None
    assert "open_support_work_item" not in harness.runtime.calls
    assert "record_support_outcome" not in harness.runtime.calls, "the drain ran on a rejection"
    # The gate is evaluated, and nothing after it is.
    assert "evaluate_case_eligibility" in harness.runtime.calls
    # And it is evaluated *as a rejection*, not skipped into a pass. Without this
    # the three assertions above are all true of a suspended gate, which is what
    # the shipped release now configures.
    assert harness.instance._state.policy is not None
    assert harness.instance._state.policy.state == PolicyGateState.EVALUATED.value
    assert harness.instance._state.policy.decision == PolicyDecisionName.REJECT.value
    # `_open_support` is the statement after the gate and holds the only two
    # `workflow.patched` calls on this path, so an empty marker log is the
    # positional guarantee restated: nothing past the gate ran at all.
    assert harness.runtime.patch_ids == []


# ---------------------------------------------------------------------------
# The two patch gates inside `_open_support`, both limbs of each
#
# `_Runtime` had no `patched` until this branch, so *every* test above stopped
# short of `_open_support` and neither gate had ever been evaluated from this
# module -- two gates, four limbs, no coverage. A `patched` that merely stopped
# raising would have made the two patched limbs reachable and left the two
# un-patched ones exactly as dark, so both sides of both are taken here.
#
# The ids are read off production rather than restated, so a renamed marker
# fails these tests instead of quietly answering a string nobody consults.
# ---------------------------------------------------------------------------

_DRAFT_MARKER = workflow_module._PATCH_STRUCTURED_SUPPORT_DRAFT
_REVIEW_MARKER = workflow_module._PATCH_SUPPORT_TEMPLATE_REVIEW_GATE


def _option_for(harness: _WorkflowHarness, activity: str) -> dict[str, Any]:
    """The keyword options `_open_support` passed with one activity call."""
    matches = [options for name, options in harness.runtime.options if name == activity]
    assert len(matches) == 1, f"expected exactly one {activity} call, saw {len(matches)}"
    return matches[0]


async def _unreachable_template_draft(_request: Any) -> TemplateReviewDraftSet:
    """A gate activity that is present so its *absence from the log* means something."""
    return TemplateReviewDraftSet(drafts=(), template_available=False)


async def test_a_new_execution_asks_the_draft_activity_for_the_typed_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_PATCH_STRUCTURED_SUPPORT_DRAFT`, patched limb (workflow line 2247).

    A new execution writes the marker and pins `result_type=SupportRequestDraft`,
    so the payload converter types the result and the structured payload reaches
    the opening message. The pin is the entire difference between the two limbs
    -- same activity, same input -- which is why the assertion is on the options
    and not on the call list.
    """
    harness = _harness(monkeypatch, patches={_DRAFT_MARKER: True, _REVIEW_MARKER: False})
    # The review marker is pinned off only to keep this test about the draft
    # gate. The stub means a review marker answered wrongly degrades to the
    # composed path rather than exploding, so the assertion that fires below
    # stays legible as "the draft gate took the wrong limb" and is never
    # replaced by a `KeyError` from a neighbouring gate.
    harness.runtime._activities["record_template_draft"] = _unreachable_template_draft

    outcome = await _run(harness, work_item_id=None)

    assert harness.runtime.patch_ids == [_DRAFT_MARKER, _REVIEW_MARKER]
    assert _option_for(harness, "draft_support_request")["result_type"] is SupportRequestDraft
    # And the typed draft is what Support was handed.
    assert outcome.work_item_id is not None
    opened = harness.fixture.support.work_items[outcome.work_item_id]
    assert opened["draft"], "the typed branch opened a thread with no request in it"


async def test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_PATCH_STRUCTURED_SUPPORT_DRAFT`, un-patched limb (workflow line 2247).

    The limb that wedged cases `return-case-7b216e58` and `return-case-2328a586`.
    A history recorded before the marker existed holds prose where the typed
    branch expects a dataclass, so this limb pins no `result_type` at all and
    hands whatever came back to `_coerce_support_draft`.

    The string is what a pre-`eaed61c` execution recorded, and the assertion is
    that it *arrives at Support unchanged* -- the failure this guards against is
    not an exception, it is a thread opened with the wrong words in it. The
    payload is empty because a string carries none, and inventing one would put
    facts on the message that nothing observed.
    """
    harness = _harness(monkeypatch, patches={_DRAFT_MARKER: False, _REVIEW_MARKER: False})
    legacy_text = "RETURN SUPPORT REQUEST\n\nCase:\n- Case ID: case-phase-4\n"

    async def _prose(_request: Any) -> Any:
        return legacy_text

    harness.runtime._activities["draft_support_request"] = _prose
    # Same reason as the test above: the neighbouring gate must not be able to
    # supply this test's failure.
    harness.runtime._activities["record_template_draft"] = _unreachable_template_draft

    outcome = await _run(harness, work_item_id=None)

    assert harness.runtime.patch_ids == [_DRAFT_MARKER, _REVIEW_MARKER]
    assert "result_type" not in _option_for(harness, "draft_support_request"), (
        "the legacy limb pinned a result type, which is the wedge it exists to avoid"
    )
    assert outcome.work_item_id is not None
    opened = harness.fixture.support.work_items[outcome.work_item_id]
    assert opened["draft"] == legacy_text
    assert workflow_module._coerce_support_draft(legacy_text).payload == {}


async def test_a_new_execution_consults_the_review_gate_before_it_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE`, patched limb (workflow line 2294).

    The marker is present, so `_template_review_gate` runs and its first act is
    `record_template_draft`. The release here has published no template, which is
    one of the gate's two documented ways of handing the outcome back
    (`template_available=False`), and the case then takes the composed path and
    opens the thread as it always did.

    That shape is chosen deliberately: it proves the gate was *entered* -- the
    activity call is the proof, and it cannot happen on the other limb -- without
    rebuilding the reviewer machinery that `tests/test_support_template_review_gate.py`
    already owns 1,200 lines of. What is new here is only which branch of
    `_open_support` ran.
    """
    harness = _harness(monkeypatch, patches={_DRAFT_MARKER: True, _REVIEW_MARKER: True})

    async def _no_template_published(_request: Any) -> TemplateReviewDraftSet:
        return TemplateReviewDraftSet(drafts=(), template_available=False)

    harness.runtime._activities["record_template_draft"] = _no_template_published

    outcome = await _run(harness, work_item_id=None)

    assert harness.runtime.patch_ids == [_DRAFT_MARKER, _REVIEW_MARKER]
    assert "record_template_draft" in harness.runtime.calls, "the gate was skipped while marked"
    assert harness.runtime.calls.index("record_template_draft") < harness.runtime.calls.index(
        "open_support_work_item"
    ), "the gate ran after the send it is supposed to gate"
    # No template, so the composed path still runs and the case is not parked.
    assert outcome.work_item_id is not None
    assert outcome.status == ReturnCaseStatus.AWAITING_SUPPORT.value


async def test_an_unmarked_history_never_reaches_the_review_gate_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE`, un-patched limb (workflow line 2294).

    The population this marker exists for: an execution recorded before the gate
    was written. Its history holds `open_support_work_item` where the gated path
    would reach `record_template_draft`, so a replay that took the gate would
    fail non-determinism on every workflow task -- the wedge on a much wider
    population than the draft marker's.

    So the claim is byte-for-byte the pre-gate behaviour, and it is asserted
    negatively *and* positionally: `record_template_draft` is never called, and
    the send is the statement immediately after the draft. The marker is still
    consulted -- that is what distinguishes "the gate answered no" from "the gate
    is not there any more", and the latter would silently pass a bare absence
    check.
    """
    harness = _harness(monkeypatch, patches={_DRAFT_MARKER: True, _REVIEW_MARKER: False})
    # Deliberately available. If the un-patched limb ever started consulting the
    # gate, this would make it succeed quietly instead of raising, so the
    # assertions below are the only thing standing between the two limbs.
    harness.runtime._activities["record_template_draft"] = _unreachable_template_draft

    outcome = await _run(harness, work_item_id=None)

    assert harness.runtime.patch_ids == [_DRAFT_MARKER, _REVIEW_MARKER]
    assert "record_template_draft" not in harness.runtime.calls
    assert (
        harness.runtime.calls.index("open_support_work_item")
        == harness.runtime.calls.index("draft_support_request") + 1
    ), "something ran between the draft and the send on the pre-gate path"
    assert outcome.work_item_id is not None
    assert outcome.status == ReturnCaseStatus.AWAITING_SUPPORT.value


async def test_a_patch_marker_this_module_does_not_know_about_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mapping refuses to answer for a marker the test did not pin.

    Rule 13 applied to the double itself. `_open_support` grew from one
    `workflow.patched` call to two; if it grows a third, the four tests above
    must fail rather than answer the new marker at random and go on asserting a
    branch nobody chose. A `KeyError` naming the unpinned id is that failure.
    """
    runtime = _Runtime({}, patches={_DRAFT_MARKER: True})

    assert runtime.patched(_DRAFT_MARKER) is True
    with pytest.raises(KeyError):
        runtime.patched(_REVIEW_MARKER)
    assert runtime.patch_ids == [_DRAFT_MARKER, _REVIEW_MARKER]
    del monkeypatch


async def test_a_resumed_case_holding_a_work_item_does_not_re_evaluate_the_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3A.7's `work_item_id is None` guard, still guarding.

    A case Support is already working on must not have its eligibility decided a
    second time -- every `continue_as_new` would otherwise re-evaluate it, and a
    rule set corrected in between could reject a return a human is holding an
    open thread about.
    """
    harness = _harness(monkeypatch)

    await _run(harness, work_item_id="wi-1")

    assert "evaluate_case_eligibility" not in harness.runtime.calls
    assert "request_bay_assignment" not in harness.runtime.calls


async def test_the_graph_sync_failure_still_parks_before_reporting_rma_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain must not weaken W2.5.

    A record in the store and absent from the graph is one Order Discovery will
    tell the associate does not exist. The drain stops on it -- it does not go on
    to the next notice, and it does not report `RMA_RECEIVED`.
    """
    arrivals: list[Callable[[ReturnCaseWorkflow], None]] = [
        lambda case: case.support_response(
            _notice(SupportReturnRecord(return_reference="RMA-1"), event_id="evt-1")
        )
    ]
    harness = _harness(monkeypatch, arrivals=arrivals)
    harness.fixture.graph_sync.should_fail = True

    outcome = await _run(harness)

    assert outcome.parked_reason == "RETURN_GRAPH_SYNC_FAILED"
    assert outcome.status != ReturnCaseStatus.RMA_RECEIVED.value


# ---------------------------------------------------------------------------
# The handoff's completeness claim (UIAUDIT-004)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_selected_item_alone_does_not_make_the_handoff_claim_completeness() -> None:
    """Support must not be told the required facts are in when they are not.

    The claim used to read `bool(selected)` -- true the moment any line was
    picked. So a case the platform itself recorded as
    `awaiting: ["RETURN_METHOD"], businessComplete: false` handed Support a
    message saying "Required Return Information: Complete", and asked them to
    issue or decline the RMA on that basis. The case pane beside it said
    "Waiting on RETURN_METHOD".

    This is that case: an item is selected and no return method has been
    established. Both halves of the handoff must say Incomplete, and the
    projection is asserted alongside them so the test fails if the two ever
    stop agreeing.
    """
    fixture = _fixture()
    fixture.repository.items.append(
        {
            "caseId": CASE_ID,
            "returnItemId": "item-1",
            "orderLineId": "1",
            "productId": "4000096",
            "quantity": 1,
            "reason": "ORDERED_IN_ERROR",
            "returnRecordId": None,
            "version": 1,
        }
    )

    projection = await fixture.repository.projection()
    assert "RETURN_METHOD" in [str(dimension) for dimension in projection.awaiting]
    assert projection.businessComplete is False

    draft = await fixture.activities.draft_support_request(
        DraftSupportRequestInput(
            case_id=CASE_ID,
            configuration_release_id="release-1",
            work_item_id="work-item-1",
        )
    )

    # The associate's half is complete -- a line, a quantity, a reason -- and
    # Support's half is not. One line each, because they were one line before
    # and it could only ever read "Incomplete": `awaiting` holds nothing an
    # associate can state, so the verdict on their work was decided by whether
    # Support had already answered the message being composed.
    assert "Required Return Information: Complete" in draft.text
    assert "Awaiting From Support: RETURN_METHOD" in draft.text
    assert draft.payload["verification"]["requiredReturnInformationComplete"] is True
    assert draft.payload["verification"]["awaitingFromSupport"] == ["RETURN_METHOD"]


@pytest.mark.asyncio
async def test_a_line_with_no_quantity_leaves_the_request_incomplete() -> None:
    """The associate's half, judged on what the associate supplies.

    Every line, not any line: a request naming a line with no quantity is one
    Support has to come back about, whatever the other lines carry. The reading
    this replaces was `bool(selected)`, which called that request complete the
    moment the line existed.
    """
    fixture = _fixture()
    fixture.repository.items.append(
        {
            "caseId": CASE_ID,
            "returnItemId": "item-1",
            "orderLineId": "1",
            "productId": "4000096",
            "quantity": 0,
            "reason": "ORDERED_IN_ERROR",
            "returnRecordId": None,
            "version": 1,
        }
    )

    draft = await fixture.activities.draft_support_request(
        DraftSupportRequestInput(
            case_id=CASE_ID,
            configuration_release_id="release-1",
            work_item_id="work-item-1",
        )
    )

    assert "Required Return Information: Incomplete" in draft.text
    assert draft.payload["verification"]["requiredReturnInformationComplete"] is False


@pytest.mark.asyncio
async def test_an_unreadable_projection_says_so_rather_than_reporting_nothing_outstanding() -> None:
    """ "We cannot tell" and "nothing outstanding" send Support to opposite actions."""
    fixture = _fixture()
    fixture.repository.items.append(
        {
            "caseId": CASE_ID,
            "returnItemId": "item-1",
            "orderLineId": "1",
            "productId": "4000096",
            "quantity": 1,
            "reason": "ORDERED_IN_ERROR",
            "returnRecordId": None,
            "version": 1,
        }
    )
    # No projection loader at all: the narrower port an activity double presents.
    fixture.repository.load_case_projection_state = None  # type: ignore[method-assign]

    draft = await fixture.activities.draft_support_request(
        DraftSupportRequestInput(
            case_id=CASE_ID,
            configuration_release_id="release-1",
            work_item_id="work-item-1",
        )
    )

    # "We cannot tell" and "nothing is outstanding" look identical as an empty
    # list and send Support to opposite actions, so the unreadable case says so
    # in words. The associate's half is still answerable -- it is read from the
    # selection, which is not what failed.
    assert "Awaiting From Support: UNKNOWN -- case state could not be read" in draft.text
    assert draft.payload["verification"]["supportStateKnown"] is False
