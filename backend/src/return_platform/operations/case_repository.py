"""The case aggregate half of the operational repository.

A mixin rather than a separate collaborator. `resolve_operational_repository`
hands callers an `OperationalRepository` and a dozen call sites read
`repository.get_case(...)`, `repository.list_return_records(...)` and the rest
directly off it; inheriting keeps that surface byte-identical, where delegation
would have rewritten every one of them to say the same thing through another
attribute.

The collections it writes are declared here and bound in
`OperationalRepository.__init__`, which owns the client, the settings and the
database handle. Declaring them without initialising them is deliberate: two
places that build the same handle are two places that can disagree about which
database it points at.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar, cast

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.operations.case_projection.assembly import (
    RETURN_RECORD_SHIPMENTS_FIELD,
    SUPPORT_WORK_ITEMS_COLLECTION,
    CaseAggregateDocuments,
    assemble_case_projection_state,
)
from return_platform.operations.case_projection.backfill import (
    CaseBackfillAction,
    CaseBackfillPlan,
    plan_case_backfill,
)
from return_platform.operations.case_projection.contract import CaseProjectionState
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import (
    CaseStatus,
    FactAcquisition,
    FactChannel,
    utc_now,
)
from return_platform.operations.order_lines.availability import (
    LineAvailability,
    compute_order_line_availability,
)
from return_platform.operations.order_lines.reservations import (
    ORDER_LINE_RESERVATIONS_COLLECTION,
    RESERVATION_LEDGER_COLLECTION,
    LineAlreadyAuthorizedError,
    LineSelection,
    QuantityReservationExpiredError,
    QuantityUnavailableError,
    ReservationRelease,
    ReservationState,
    ReservationView,
    SelectionOutcome,
    ledger_id,
    new_reservation_document,
    read_reservation,
)

_T = TypeVar("_T")


class CaseRepository:
    """Case, case facts, return records and case return items."""

    cases: AsyncCollection[dict[str, object]]
    case_facts: AsyncCollection[dict[str, object]]
    return_records: AsyncCollection[dict[str, object]]
    return_items: AsyncCollection[dict[str, object]]
    #: Bound by `OperationalRepository.__init__`, like the collections above.
    #: Declared and not initialised for the same reason they are: two places
    #: that build the same handle are two places that can disagree.
    _client: AsyncMongoClient[dict[str, object]]

    # ------------------------------------------------------------------
    # Revision atomicity (plan sect. 6.5)
    # ------------------------------------------------------------------

    async def bump_case_revision(
        self, case_id: str, *, session: AsyncClientSession, when: datetime | None = None
    ) -> bool:
        """Advance `cases.version` and `cases.updatedAt` for a child write.

        > Any write that can change the `CaseDetail` projection must, in the
        > same transaction, bump `case.revision` and set `case.updatedAt`.

        `session` is required and has no default. The invariant is not "the
        revision moves eventually" -- it is that the revision moves *with* the
        child write, so a caller that has no transaction to enrol in cannot
        satisfy it and should not be able to call this and believe it did. A
        second, best-effort write is the failure mode plan sect. 6.5 names.

        No `expected_version`: this is a blind `$inc`, not a compare-and-set.
        The writer is a child collection with no opinion about the case
        document's contents, and an optimistic round trip here would turn every
        concurrent Support reply into a spurious conflict on a field neither
        writer touched. Two concurrent `$inc`s inside transactions conflict at
        the server, one aborts as transient, `with_transaction` re-runs it, and
        the two revisions come out distinct and increasing -- which is the
        lost-update property the plan asks for, obtained from the storage engine
        rather than from a read-modify-write.

        Returns whether a case document matched. A child row whose case has
        already been deleted is on no `CaseDetail` projection, so there is
        nothing for a client to invalidate and nothing to raise about.
        """
        result = await self.cases.update_one(
            {"caseId": case_id},
            {"$inc": {"version": 1}, "$set": {"updatedAt": when or utc_now()}},
            session=session,
        )
        return result.matched_count == 1

    async def _in_transaction(self, callback: Callable[[AsyncClientSession], Awaitable[_T]]) -> _T:
        """Run `callback` as one multi-document transaction.

        `with_transaction` rather than a bare `start_transaction`, and the retry
        is the point: a child write and a revision bump on the same case *will*
        collide with a second writer's bump, and MongoDB labels that abort
        `TransientTransactionError` precisely so the driver can re-run the whole
        callback. A bare transaction would surface it to the caller as a write
        conflict on a case nobody edited.

        Every callback below is therefore written to be re-runnable: each builds
        its inserts from documents captured before the transaction opens, and
        the previous attempt's writes are rolled back before the next one
        starts. An error the driver does not label transient -- a duplicate key
        on a replayed id, a version mismatch -- aborts and propagates, taking
        the revision bump with it. That is the atomicity half of the invariant:
        a child write that failed leaves the revision where it was.
        """
        async with self._client.start_session() as mongo_session:
            return await mongo_session.with_transaction(callback)

    # ------------------------------------------------------------------
    # Case aggregate
    # ------------------------------------------------------------------

    async def create_case(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        branch_id: str | None = None,
        channel_a_conversation_id: str | None = None,
        confirmed_order_reference: str | None = None,
        configuration_release_id: str | None = None,
        graph_generation_id: str | None = None,
        confirmation_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a case, or return the existing one for the same confirmation.

        Idempotent on `confirmationKey` rather than on `caseId`: the caller is a
        turn Temporal may retry, and two attempts of the same confirmation must
        produce one case. The unique partial index is what enforces it -- this
        catches the duplicate and reads back the winner rather than racing a
        find-then-insert.

        Not keyed on the conversation: one conversation confirming two different
        orders is two returns, and collapsing them would silently discard the
        second.
        """
        now = utc_now()
        document = {
            "caseId": case_id,
            "tenantId": tenant_id,
            "principalId": principal_id,
            "branchId": branch_id,
            "status": CaseStatus.GATHERING_INFO.value,
            "channelAConversationId": channel_a_conversation_id,
            "channelBWorkItemId": None,
            "confirmedOrderReference": confirmed_order_reference,
            "confirmationKey": confirmation_key,
            "sessionId": None,
            "workflowId": None,
            "configurationReleaseId": configuration_release_id,
            "graphGenerationId": graph_generation_id,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.cases.insert_one(dict(document))
        except DuplicateKeyError:
            existing = None
            if confirmation_key is not None:
                existing = await self.cases.find_one({"confirmationKey": confirmation_key})
            if existing is None:
                existing = await self.cases.find_one({"caseId": case_id})
            if existing is None:  # pragma: no cover - duplicate on neither key
                raise
            return cast(dict[str, Any], existing)
        return document

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        document = await self.cases.find_one({"caseId": case_id})
        return cast(dict[str, Any], document) if document is not None else None

    async def find_case_by_confirmation(self, confirmation_key: str) -> dict[str, Any] | None:
        document = await self.cases.find_one({"confirmationKey": confirmation_key})
        return cast(dict[str, Any], document) if document is not None else None

    async def get_case_by_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str | None = None,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Channel A -> case. What the copilot needs to show a return's state.

        Most recent first: a conversation that confirmed two different orders
        has two cases, and the one the associate is working on is the latest.

        Scope belongs in the filter, not in a check afterwards. A conversation
        id is guessable, and a caller that reads the document first has already
        read someone else's case by the time it decides not to return it.
        """
        query: dict[str, Any] = {"channelAConversationId": conversation_id}
        if tenant_id is not None:
            query["tenantId"] = tenant_id
        if principal_id is not None:
            query["principalId"] = principal_id
        document = await self.cases.find_one(query, sort=[("createdAt", DESCENDING)])
        return cast(dict[str, Any], document) if document is not None else None

    async def get_case_by_work_item(self, work_item_id: str) -> dict[str, Any] | None:
        """Channel B -> case. The other half of the link, and the one that makes
        a support outcome reachable from the associate's conversation."""
        document = await self.cases.find_one({"channelBWorkItemId": work_item_id})
        return cast(dict[str, Any], document) if document is not None else None

    async def list_cases_for_principal(
        self, *, tenant_id: str, principal_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        cursor = (
            self.cases.find({"tenantId": tenant_id, "principalId": principal_id})
            .sort("updatedAt", DESCENDING)
            .limit(limit)
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def bind_case_workflow(self, case_id: str, *, workflow_id: str) -> bool:
        """Record the durable execution that owns this case. Idempotent.

        Deliberately not `update_case(..., expected_version=)`: the writer is
        the confirmation that has just started the workflow, and it holds no
        version -- while the workflow it started is already writing statuses
        against the same document. An optimistic-concurrency round trip here
        would lose that race routinely and report a link failure for a case
        that is running perfectly well.

        `workflowId: None` in the filter is what makes it write once. A second
        call with the same id matches nothing, finds the id already recorded
        and reports success; a call with a *different* id for a case that has
        one is a broken invariant (the id is derived from the case, so two
        cannot exist) and raises rather than silently repointing the case at
        another execution.
        """
        result = await self.cases.update_one(
            {"caseId": case_id, "workflowId": None},
            {"$set": {"workflowId": workflow_id, "updatedAt": utc_now()}, "$inc": {"version": 1}},
        )
        if result.modified_count == 1:
            return True
        existing = await self.cases.find_one({"caseId": case_id}, {"workflowId": 1})
        if existing is None:
            raise KeyError(case_id)
        recorded = existing.get("workflowId")
        if recorded == workflow_id:
            return False
        raise ConcurrencyConflictError(case_id)

    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Confirmed cases whose durable execution was never recorded.

        The queue `return_case_recovery` drains. Terminal cases are excluded:
        only `ReturnCaseWorkflow` sets `CLOSED`, `CANCELLED` or `POLICY_REJECTED`,
        so one that reached any of them has had its workflow whatever the link
        says, and starting a fresh execution for it would reopen a finished
        return -- for `POLICY_REJECTED`, one the policy gate finished on purpose.
        """
        cursor = (
            self.cases.find(
                {
                    "workflowId": None,
                    "status": {
                        "$nin": [
                            CaseStatus.CLOSED.value,
                            CaseStatus.CANCELLED.value,
                            CaseStatus.POLICY_REJECTED.value,
                        ],
                    },
                    "createdAt": {"$lt": created_before},
                }
            )
            .sort("createdAt", ASCENDING)
            .limit(limit)
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def update_case(
        self, case_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        document = await self.cases.find_one_and_update(
            {"caseId": case_id, "version": expected_version},
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            if await self.cases.find_one({"caseId": case_id}, {"_id": 1}) is None:
                raise KeyError(case_id)
            raise ConcurrencyConflictError(case_id)
        return cast(dict[str, Any], document)

    async def append_case_fact(
        self,
        *,
        fact_id: str,
        case_id: str,
        fact_name: str,
        value: Any,
        agent_id: str,
        channel: FactChannel,
        acquisition_method: FactAcquisition,
        turn_id: str | None = None,
        source_system: str | None = None,
        source_path: str | None = None,
        observed_at: datetime | None = None,
        supersedes_fact_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one observation. Never updates an existing one.

        Insert-only by construction: Bay, Support, Fulfillment and Channel A all
        write concurrently, and an update-in-place would make the last writer
        win and drop what the others learned. Two writers here both succeed, and
        `latest_case_facts` decides which is current.

        A fact is on the `CaseDetail` projection, so the case revision moves in
        the same transaction (plan sect. 6.5). A replayed fact is not: every
        derived `factId` on the workflow paths is unique-indexed, the duplicate
        aborts the transaction, and `_append_fact_once` absorbs it -- the
        redelivery records nothing and invalidates nobody's cache.
        """
        now = utc_now()
        document = {
            "factId": fact_id,
            "caseId": case_id,
            "factName": fact_name,
            "value": value,
            "agentId": agent_id,
            "channel": channel.value,
            "turnId": turn_id,
            "sourceSystem": source_system,
            "sourcePath": source_path,
            "acquisitionMethod": acquisition_method.value,
            "observedAt": observed_at or now,
            "recordedAt": now,
            "supersedesFactId": supersedes_fact_id,
            "correlationId": correlation_id,
        }

        async def _write(session: AsyncClientSession) -> None:
            await self.case_facts.insert_one(dict(document), session=session)
            await self.bump_case_revision(case_id, session=session, when=now)

        await self._in_transaction(_write)
        return document

    async def append_scoped_case_fact(
        self,
        *,
        fact_id: str,
        case_id: str,
        fact_name: str,
        value: Any,
        agent_id: str,
        channel: FactChannel,
        acquisition_method: FactAcquisition,
        record_scope: str | None,
        identity_version: int,
        actor_id: str | None = None,
        turn_id: str | None = None,
        source_system: str | None = None,
        source_path: str | None = None,
        observed_at: datetime | None = None,
        supersedes_fact_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one observation about one return record -- or, scope `None`,
        about the case.

        The scoped sibling of `append_case_fact`, and a sibling deliberately
        rather than two new parameters on it: the legacy write path and every
        document it has ever produced stay byte-identical, so a pre-deploy
        fact replayed through pre-deploy code cannot change shape. This path
        additionally stores `record_scope` (which record the fact is about),
        `identity_version` (which fact-identity derivation produced `fact_id`)
        and `actorId` (on whose authority, when a command caused the fact), and
        everything else -- insert-only against the unique `factId`, the plan
        sect. 6.5 revision bump in the same transaction -- is the same contract
        `append_case_fact` documents.

        `actor_id` is the *server-stamped* principal contracts.md sect. 4
        requires of a command-originated fact, and it is deliberately not part
        of the fact's identity: `fact_id` arrives already derived, and this
        method does not touch it. Provenance describes a fact; it does not
        multiply it. Two arrivals of one observation under two principals are
        one fact whose recorded actor is whoever got there first -- the same
        append-once semantics every other field on this path has -- and not two
        facts that a projection would then have to choose between. It defaults
        to `None` because most facts are observations, which have no actor at
        all, and because every caller that predates the field keeps working.
        """
        now = utc_now()
        document = {
            "factId": fact_id,
            "caseId": case_id,
            "factName": fact_name,
            "value": value,
            "agentId": agent_id,
            "channel": channel.value,
            "turnId": turn_id,
            "sourceSystem": source_system,
            "sourcePath": source_path,
            "acquisitionMethod": acquisition_method.value,
            "observedAt": observed_at or now,
            "recordedAt": now,
            "supersedesFactId": supersedes_fact_id,
            "correlationId": correlation_id,
            "record_scope": record_scope,
            "identity_version": identity_version,
            # Always written, `None` included: a reader distinguishing "no
            # actor" from "field absent" is the difference between provenance
            # and a gap, and every document this path has ever written carries
            # the key either way.
            "actorId": actor_id,
        }

        async def _write(session: AsyncClientSession) -> None:
            await self.case_facts.insert_one(dict(document), session=session)
            await self.bump_case_revision(case_id, session=session, when=now)

        await self._in_transaction(_write)
        return document

    async def list_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        """The whole log, oldest first. The audit read."""
        cursor = self.case_facts.find({"caseId": case_id}).sort("recordedAt", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        """Current state, projected from the log: newest record per fact name.

        A projection rather than a stored document, so adding a writer cannot
        clobber a value it did not know about. Ties on `recordedAt` -- two
        writers inside the same clock tick -- break on `factId`, which is
        arbitrary but stable, so the projection is at least deterministic.
        """
        latest: dict[str, dict[str, Any]] = {}
        for document in await self.list_case_facts(case_id):
            name = str(document["factName"])
            current = latest.get(name)
            if current is None:
                latest[name] = document
                continue
            if (document["recordedAt"], str(document["factId"])) >= (
                current["recordedAt"],
                str(current["factId"]),
            ):
                latest[name] = document
        return latest

    async def latest_case_facts_scoped(
        self, case_id: str
    ) -> dict[tuple[str | None, str], dict[str, Any]]:
        """Current state per `(record_scope, factName)`: newest record each.

        `latest_case_facts` collapses the log per fact name, which is right
        for case-level state and wrong the moment two RMAs each carry a fact
        of the same name -- one record's tracking number would shadow the
        other's. This projection partitions by scope first, so each record
        has its own latest value and the `None` partition is exactly the
        case-level view. A legacy fact stores no `record_scope` key at all;
        `.get` reads that as `None`, which is the case-level partition it has
        always belonged to. Same tie-break as `latest_case_facts`:
        `(recordedAt, factId)`, deterministic if arbitrary.

        A separate method rather than a change to `latest_case_facts`
        deliberately -- its consumers read a per-name mapping and are
        converged onto this one as a registered follow-up, not here.
        """
        latest: dict[tuple[str | None, str], dict[str, Any]] = {}
        for document in await self.list_case_facts(case_id):
            scope = document.get("record_scope")
            key = (str(scope) if scope is not None else None, str(document["factName"]))
            current = latest.get(key)
            if current is None:
                latest[key] = document
                continue
            if (document["recordedAt"], str(document["factId"])) >= (
                current["recordedAt"],
                str(current["factId"]),
            ):
                latest[key] = document
        return latest

    async def create_return_record(
        self,
        *,
        return_record_id: str,
        case_id: str,
        return_reference: str | None = None,
        status: str = "DRAFT",
        source_system: str | None = None,
    ) -> dict[str, Any]:
        """One RMA on a case, with the case revision moved to match.

        The bump shares the insert's transaction (plan sect. 6.5): an RMA
        appearing is the single largest change the `CaseDetail` projection ever
        makes, and a client that could not see it in the revision could not tell
        a fresh projection from a stale one.

        **A replay must not bump.** `record_support_outcome` supplies
        pre-minted ids and re-runs with them, and the unique partial index on
        `(caseId, returnReference)` is what turns the second attempt into a
        no-op. That duplicate now aborts this transaction rather than merely
        failing an insert, so the revision the first attempt moved stays where
        it was and the redelivery invalidates nothing. The `DuplicateKeyError`
        still reaches the caller unchanged -- absorbing it is the activity's
        decision, not the repository's.
        """
        now = utc_now()
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
            "createdAt": now,
            "updatedAt": now,
        }

        async def _write(session: AsyncClientSession) -> None:
            await self.return_records.insert_one(dict(document), session=session)
            await self.bump_case_revision(case_id, session=session, when=now)

        await self._in_transaction(_write)
        return document

    async def list_return_records(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self.return_records.find({"caseId": case_id}).sort("createdAt", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def update_return_record(
        self, return_record_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        """Move one RMA's own fields, and the case revision with them.

        The record carries the label, the tracking reference and the return
        location, so every one of these writes changes the projection. The case
        id comes from the updated document rather than from a parameter: the
        callers hold a record id and not both, and re-deriving it inside the
        transaction is what keeps the two from being able to disagree.

        A version mismatch raises from *inside* the transaction, so the bump
        rolls back with the failed update. The loser of a compare-and-set has
        changed nothing and must move no revision.
        """
        now = utc_now()

        async def _write(session: AsyncClientSession) -> dict[str, Any]:
            document = await self.return_records.find_one_and_update(
                {"returnRecordId": return_record_id, "version": expected_version},
                {"$set": {**updates, "updatedAt": now}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if document is None:
                exists = await self.return_records.find_one(
                    {"returnRecordId": return_record_id}, {"_id": 1}, session=session
                )
                if exists is None:
                    raise KeyError(return_record_id)
                raise ConcurrencyConflictError(return_record_id)
            case_id = document.get("caseId")
            if isinstance(case_id, str) and case_id:
                await self.bump_case_revision(case_id, session=session, when=now)
            return cast(dict[str, Any], document)

        return await self._in_transaction(_write)

    async def record_case_shipment(
        self,
        *,
        return_record_id: str,
        tracking_reference: str,
        carrier: str | None = None,
    ) -> bool:
        """Record one parcel on one RMA, and move the case revision with it.

        The Mongo half of a carrier update (D1). `dbo.return_tracking` is the
        authoritative return shipment store and has been RMA-scoped and plural
        since `006_return_shipment_state.sql`; what it had no counterpart for was
        the case aggregate, which is the only thing
        `load_case_projection_state` reads. So an APPLIED update persisted, synced,
        resolved its case and appended its facts, and
        `returnRecords[].shipments` stayed empty -- a Copilot polling for
        tracking after a carrier event polled forever, because the platform held
        the answer and the read model could not see it.

        **Plural by construction.** `shipments[]` holds one entry per parcel,
        keyed on the tracking reference, so a split return going back in two
        parcels is two entries rather than a second value overwriting the first.
        Nothing here writes the record's legacy `trackingReference`: that column
        is what *Support* stated, this is what a *carrier* filed, and collapsing
        the two would let a carrier event silently rewrite Support's answer.
        `project_shipments` unions them on the tracking reference instead.

        **Idempotent, and a replay bumps nothing.** Both statements below carry
        the change in their filter -- the first matches only an entry whose
        carrier differs, the second only a record that does not already hold this
        parcel -- so re-recording a parcel the record already holds matches
        nothing, writes nothing and moves no revision. That matters because
        `ReturnShipmentStateService.observe_shipment` is also the canonical read,
        reachable without any carrier event, and a read that invalidated every
        client's cache would be worse than the defect it closes.

        The bump shares the write's transaction (plan sect. 6.5): a package
        appearing on an RMA changes `returnRecords[].shipments`, `awaiting` and
        `businessComplete`, and a client that could not see it in the revision
        could not tell a fresh projection from a stale one.

        Returns whether a parcel was recorded or corrected. `False` for a record
        this case store has never seen -- `dbo.return_tracking` is keyed on the
        RMA and needs no `dbo.return_record` row, so a carrier event for an RMA
        with no case is a real state the service already reports rather than an
        error -- and `False` for a replay that changed nothing.
        """
        now = utc_now()
        entry = {
            "trackingReference": tracking_reference,
            "carrier": carrier,
            "createdAt": now,
            "updatedAt": now,
        }

        async def _write(session: AsyncClientSession) -> bool:
            written = False
            # The parcel is already here and the carrier the feed filed differs
            # from the one stored. Correcting it is the only field-level change
            # this writer can make; the carrier's *status* is deliberately not
            # stored, because the projection has no honest home for an open
            # carrier vocabulary and the reading of it reaches the case as the
            # `fulfillment_status` fact instead.
            #
            # Only ever *to* a carrier, never to `None`. A feed that files a scan
            # without a carrier code has said nothing about the carrier, and
            # applying that silence over one already recorded would delete it --
            # the merge rule `RETURN_RECORD_MERGED_FIELDS` states for exactly
            # this field, and the reason `008_return_record_carrier.sql` gives
            # for a later reply not blanking an earlier answer.
            if carrier is not None:
                corrected = await self.return_records.update_one(
                    {
                        "returnRecordId": return_record_id,
                        RETURN_RECORD_SHIPMENTS_FIELD: {
                            "$elemMatch": {
                                "trackingReference": tracking_reference,
                                "carrier": {"$ne": carrier},
                            }
                        },
                    },
                    {
                        "$set": {
                            f"{RETURN_RECORD_SHIPMENTS_FIELD}.$.carrier": carrier,
                            f"{RETURN_RECORD_SHIPMENTS_FIELD}.$.updatedAt": now,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                written = corrected.matched_count == 1
            if not written:
                # A parcel this record has never held. The `$ne` in the filter is
                # what makes a concurrent writer's identical push lose rather
                # than duplicate the parcel: the loser matches nothing, and two
                # entries for one tracking number would project as two parcels.
                appended = await self.return_records.update_one(
                    {
                        "returnRecordId": return_record_id,
                        f"{RETURN_RECORD_SHIPMENTS_FIELD}.trackingReference": {
                            "$ne": tracking_reference
                        },
                    },
                    {
                        "$push": {RETURN_RECORD_SHIPMENTS_FIELD: dict(entry)},
                        "$set": {"updatedAt": now},
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                written = appended.matched_count == 1
            if not written:
                return False
            document = await self.return_records.find_one(
                {"returnRecordId": return_record_id}, {"caseId": 1}, session=session
            )
            case_id = None if document is None else document.get("caseId")
            if isinstance(case_id, str) and case_id:
                await self.bump_case_revision(case_id, session=session, when=now)
            return True

        return await self._in_transaction(_write)

    async def create_case_return_item(
        self,
        *,
        return_item_id: str,
        case_id: str,
        return_record_id: str | None,
        order_line_reference: str,
        product_reference: str | None = None,
        quantity: int = 1,
        reason: str | None = None,
        condition: str | None = None,
        package_reference: str | None = None,
    ) -> dict[str, Any]:
        """An item on a case, optionally already assigned to a return record.

        `return_record_id` is nullable because the two are learned at different
        times: the associate names the lines they are returning before Support
        has said how many RMAs those lines will become. Forcing the association
        up front would mean inventing a record to hold them.

        Written into `operational_return_items` rather than a second item
        collection. That collection is already the item list, already uniquely
        indexed, and already read by three call sites; a parallel one would be
        the duplication this programme is removing.

        The case revision moves in the same transaction (plan sect. 6.5) --
        `selectedItems` is on the projection -- and a re-selection that
        duplicates an existing item id aborts the transaction and moves nothing.
        """
        now = utc_now()
        document = {
            "returnItemId": return_item_id,
            "caseId": case_id,
            "returnRecordId": return_record_id,
            "orderLineId": order_line_reference,
            "productReference": product_reference,
            "quantity": quantity,
            "reason": reason,
            "condition": condition,
            "packageReference": package_reference,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }

        async def _write(session: AsyncClientSession) -> None:
            await self.return_items.insert_one(dict(document), session=session)
            await self.bump_case_revision(case_id, session=session, when=now)

        await self._in_transaction(_write)
        return document

    async def list_case_return_items(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self.return_items.find({"caseId": case_id}).sort("createdAt", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    # ------------------------------------------------------------------
    # The Copilot read projection (plan sect. 6.3)
    # ------------------------------------------------------------------

    async def load_case_projection_state(self, case_id: str) -> CaseProjectionState | None:
        """One case as the Copilot contract sees it, or `None` if there is no such case.

        The **only** sanctioned way to get a `CaseProjectionState` out of
        persistence. Everything it does with what it reads is in
        `case_projection/assembly.py`, which is pure -- this method is the four
        reads and nothing else, so the rules that decide what a case *means* can
        be tested without a database and the IO can be reviewed on one screen.

        Pass the result to `project_case` to obtain the wire contract. That
        function is where `stage`, `awaiting`, `businessComplete` and
        `isTerminal` come from, and it is deliberately not called here: a
        repository that returned the derived shape would be a second place stage
        logic could grow.

        **The case document is read first, and that order is load-bearing.**
        These are four separate reads and they are not a snapshot, so a
        concurrent child write can land between any two of them however the
        revision behaves. Reading the case first means the reported `revision`
        can only be *older* than the children -- a client comparing revisions
        discards the response and polls again. The other order reports a fresh
        revision over stale children, and the client accepts it and stops
        looking.

        Every child writer now moves the revision inside its own transaction
        (plan sect. 6.5), which is what makes the client's comparison mean
        something. It does not make this ordering redundant: the invariant says
        the revision is never *behind* the children a writer committed, not that
        a four-read sequence sees one instant. Stale-and-detectable stays the
        safe direction, so the order below does not change.

        `None` rather than a raise for a missing case: the route answers 404 and
        `get_case` already says absence this way, so a guessed id confirms
        nothing.
        """
        case = await self.get_case(case_id)
        if case is None:
            return None
        facts = await self.latest_case_facts(case_id)
        records = await self.list_return_records(case_id)
        items = await self.list_case_return_items(case_id)
        support = await self._load_support_work_item(case)
        return assemble_case_projection_state(
            CaseAggregateDocuments(
                case=case,
                facts=facts,
                return_records=tuple(records),
                return_items=tuple(items),
                support_work_item=support,
            )
        )

    async def _load_support_work_item(self, case: dict[str, Any]) -> dict[str, Any] | None:
        """The Channel B work item this case points at, if it points at one.

        Read through the case's own database handle rather than a collection
        declared on this mixin: `support_work_items` belongs to
        `operations/return_support/service.py`, and binding it here would make
        two modules responsible for the same collection. The name is a constant
        in `case_projection/assembly.py` so the two spellings cannot drift into
        a read that silently finds nothing.
        """
        work_item_id = case.get("channelBWorkItemId")
        if not isinstance(work_item_id, str) or not work_item_id.strip():
            return None
        collection = self.cases.database[SUPPORT_WORK_ITEMS_COLLECTION]
        document = await collection.find_one({"_id": work_item_id})
        return cast(dict[str, Any], document) if document is not None else None

    # ------------------------------------------------------------------
    # Order lines, returnable quantity and reservations (plan sect. 12)
    # ------------------------------------------------------------------

    @property
    def _order_line_reservations(self) -> AsyncCollection[dict[str, object]]:
        """The reservation collection, read through the case's own handle.

        Declared as a property rather than bound in `OperationalRepository.__init__`
        for the same reason `_load_support_work_item` reaches for
        `support_work_items` this way: the collection belongs to
        `operations/order_lines/reservations.py`, which owns its shape, its
        states and its indexes. Binding it beside `self.cases` would make two
        modules responsible for one collection. The name is a constant over
        there, so the two spellings cannot drift into a read that finds nothing.
        """
        return self.cases.database[ORDER_LINE_RESERVATIONS_COLLECTION]

    @property
    def _order_line_ledger(self) -> AsyncCollection[dict[str, object]]:
        """The per-line serialization token. Carries no quantity; see its module."""
        return self.cases.database[RESERVATION_LEDGER_COLLECTION]

    async def _order_case_statuses(
        self,
        *,
        tenant_id: str,
        order_reference: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """Every case in this tenant that confirmed this order -> its status.

        Tenant-scoped, and that is a correctness statement rather than a
        permission one: two tenants whose source systems both issue order number
        `CW273354` have two different orders, and summing their returns would
        make each tenant's line look shorter than it is.
        """
        cursor = self.cases.find(
            {"tenantId": tenant_id, "confirmedOrderReference": order_reference},
            {"caseId": 1, "status": 1},
            session=session,
        )
        return {
            str(document["caseId"]): document.get("status")
            async for document in cursor
            if isinstance(document.get("caseId"), str)
        }

    async def _order_line_items(
        self,
        *,
        case_ids: Collection[str],
        line_references: Collection[str],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        if not case_ids or not line_references:
            return []
        cursor = self.return_items.find(
            {"caseId": {"$in": list(case_ids)}, "orderLineId": {"$in": list(line_references)}},
            session=session,
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def _active_line_reservations(
        self,
        *,
        tenant_id: str,
        order_reference: str,
        line_references: Collection[str],
        session: AsyncClientSession | None = None,
    ) -> list[ReservationView]:
        """Only `ACTIVE` holds. Settled ones contribute nothing to the arithmetic.

        Expiry is applied on read by `is_held`, not by this filter: an `ACTIVE`
        document past its deadline must still be *seen* here, because the writer
        needs to know it exists in order to release and replace it.
        """
        if not line_references:
            return []
        cursor = self._order_line_reservations.find(
            {
                "tenantId": tenant_id,
                "orderReference": order_reference,
                "orderLineReference": {"$in": list(line_references)},
                "state": ReservationState.ACTIVE.value,
            },
            session=session,
        )
        views: list[ReservationView] = []
        async for document in cursor:
            view = read_reservation(document)
            if view is not None:
                views.append(view)
        return views

    async def load_order_line_availability(
        self,
        *,
        tenant_id: str,
        order_reference: str,
        viewing_case_id: str | None,
        ordered_by_line: Mapping[str, int | None],
        now: datetime | None = None,
        session: AsyncClientSession | None = None,
    ) -> dict[str, LineAvailability]:
        """Returnable quantity for every line of one order (plan sect. 12.2).

        Three reads and one pure function. The arithmetic is entirely in
        `order_lines/availability.py`, which is why the same computation can be
        re-run inside the selection writer's transaction without a second
        implementation of what "available" means.

        `ordered_by_line` is supplied by the caller rather than read here: the
        ordered quantities come from the source document, and a repository that
        fetched them would have to know which source asset the order lives on and
        which schema declares its paths -- both of which the order-line module
        already resolves, once, for the route.
        """
        moment = now or utc_now()
        statuses = await self._order_case_statuses(
            tenant_id=tenant_id, order_reference=order_reference, session=session
        )
        items = await self._order_line_items(
            case_ids=statuses.keys(), line_references=ordered_by_line.keys(), session=session
        )
        reservations = await self._active_line_reservations(
            tenant_id=tenant_id,
            order_reference=order_reference,
            line_references=ordered_by_line.keys(),
            session=session,
        )
        return compute_order_line_availability(
            ordered_by_line=ordered_by_line,
            items=items,
            case_status_by_id=statuses,
            reservations=reservations,
            now=moment,
            viewing_case_id=viewing_case_id,
        )

    async def list_case_reservations(
        self, case_id: str, *, states: Collection[ReservationState] | None = None
    ) -> tuple[ReservationView, ...]:
        """One case's holds, newest last. The audit read and the release input."""
        query: dict[str, Any] = {"caseId": case_id}
        if states is not None:
            query["state"] = {"$in": [state.value for state in states]}
        cursor = self._order_line_reservations.find(query).sort("createdAt", ASCENDING)
        views: list[ReservationView] = []
        async for document in cursor:
            view = read_reservation(document)
            if view is not None:
                views.append(view)
        return tuple(views)

    async def replace_case_line_selection(
        self,
        *,
        case_id: str,
        tenant_id: str,
        order_reference: str,
        selections: Sequence[LineSelection],
        ordered_by_line: Mapping[str, int | None],
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> SelectionOutcome:
        """Persist the case's selection, holding the quantity, or refuse it.

        **Replace-set, not append.** The payload is the whole selection, so a
        line the associate removed loses its item and its hold in the same
        transaction that records the ones they kept. Appending would leave a
        withdrawn line holding quantity nobody was going to return, which is the
        leak the reservation lifecycle exists to prevent.

        **The re-evaluation is the point.** Displaying a returnable quantity is
        a read, so two associates can both see 2 and both submit 2 (plan
        sect. 12.4). This recomputes availability *inside* the transaction and
        commits or refuses atomically; the loser gets
        `QuantityUnavailableError` carrying the recomputed figures so the client
        re-renders rather than guesses.

        **Why the ledger touch comes first.** A MongoDB transaction's reads take
        no locks, so recomputing inside a transaction is not by itself enough:
        two transactions can each read "2 available" from their own consistent
        snapshot and each commit a hold. Incrementing a per-line token before
        reading makes the two collide on one document; the loser aborts as a
        transient error, `with_transaction` re-runs the whole callback, and the
        re-run reads the winner's committed reservation and refuses. The token
        documents are created before the transaction opens because a concurrent
        *upsert* on the same `_id` raises a duplicate key that is not labelled
        transient -- which would surface a serialization detail as a 500.

        **The revision moves once, and only if something moved** (plan
        sect. 6.5): `selectedItems` is on the `CaseDetail` projection, and a
        re-submitted identical selection changes nothing and must invalidate
        nobody's cache.
        """
        moment = now or utc_now()
        requested = {selection.order_line_reference: selection for selection in selections}
        if len(requested) != len(selections):
            raise ValueError("a selection names the same order line twice")
        # Every line the write could touch: the ones being asked for, and the
        # ones this case currently holds and may be about to give back.
        held = await self.list_case_reservations(case_id, states=(ReservationState.ACTIVE,))
        affected = sorted({*requested, *(view.order_line_reference for view in held)})
        if not affected:
            case = await self.get_case(case_id)
            return SelectionOutcome(
                case_id=case_id,
                revision=int((case or {}).get("version", 0)),
                changed=False,
                items=(),
                reservations=(),
            )
        await self._ensure_line_tokens(
            tenant_id=tenant_id, order_reference=order_reference, lines=affected, now=moment
        )

        async def _write(session: AsyncClientSession) -> SelectionOutcome:
            return await self._apply_selection(
                session,
                case_id=case_id,
                tenant_id=tenant_id,
                order_reference=order_reference,
                requested=requested,
                affected=affected,
                ordered_by_line=ordered_by_line,
                ttl_seconds=ttl_seconds,
                now=moment,
            )

        return await self._in_transaction(_write)

    async def _ensure_line_tokens(
        self,
        *,
        tenant_id: str,
        order_reference: str,
        lines: Sequence[str],
        now: datetime,
    ) -> None:
        """Create the serialization tokens outside any transaction. Idempotent."""
        for line in lines:
            await self._order_line_ledger.update_one(
                {
                    "_id": ledger_id(
                        tenant_id=tenant_id,
                        order_reference=order_reference,
                        order_line_reference=line,
                    )
                },
                {
                    "$setOnInsert": {
                        "tenantId": tenant_id,
                        "orderReference": order_reference,
                        "orderLineReference": line,
                        "createdAt": now,
                    }
                },
                upsert=True,
            )

    async def _apply_selection(
        self,
        session: AsyncClientSession,
        *,
        case_id: str,
        tenant_id: str,
        order_reference: str,
        requested: Mapping[str, LineSelection],
        affected: Sequence[str],
        ordered_by_line: Mapping[str, int | None],
        ttl_seconds: int,
        now: datetime,
    ) -> SelectionOutcome:
        """The transaction body. Re-runnable: it reads everything it decides on."""
        for line in affected:
            # Sorted by the caller, so two writers touching overlapping lines
            # take them in the same order and contend on the first shared one
            # rather than half way through each other's work.
            await self._order_line_ledger.update_one(
                {
                    "_id": ledger_id(
                        tenant_id=tenant_id,
                        order_reference=order_reference,
                        order_line_reference=line,
                    )
                },
                {"$inc": {"sequence": 1}, "$set": {"updatedAt": now}},
                session=session,
            )

        current_items = {
            str(document["orderLineId"]): document
            for document in await self._order_line_items(
                case_ids=(case_id,), line_references=affected, session=session
            )
            if isinstance(document.get("orderLineId"), str)
        }
        authorized = {
            line: str(document["returnRecordId"])
            for line, document in current_items.items()
            if isinstance(document.get("returnRecordId"), str) and line in requested
        }
        if authorized:
            raise LineAlreadyAuthorizedError(case_id, authorized)

        availability = await self.load_order_line_availability(
            tenant_id=tenant_id,
            order_reference=order_reference,
            viewing_case_id=case_id,
            ordered_by_line={line: ordered_by_line.get(line) for line in affected},
            now=now,
            session=session,
        )
        short = {
            line: availability[line].returnable_quantity
            for line, selection in requested.items()
            if selection.quantity > availability[line].returnable_quantity
        }
        if short:
            raise QuantityUnavailableError(order_reference=order_reference, unavailable=short)

        held = {
            view.order_line_reference: view
            for view in await self._active_line_reservations(
                tenant_id=tenant_id,
                order_reference=order_reference,
                line_references=affected,
                session=session,
            )
            if view.case_id == case_id
        }
        changed = await self._write_reservations(
            session,
            case_id=case_id,
            tenant_id=tenant_id,
            order_reference=order_reference,
            requested=requested,
            held=held,
            now=now,
            ttl_seconds=ttl_seconds,
        )
        changed = (
            await self._write_selected_items(
                session,
                case_id=case_id,
                requested=requested,
                current=current_items,
                now=now,
            )
            or changed
        )
        if changed:
            await self.bump_case_revision(case_id, session=session, when=now)

        case = await self.cases.find_one({"caseId": case_id}, {"version": 1}, session=session)
        items = await self._order_line_items(
            case_ids=(case_id,), line_references=sorted(requested), session=session
        )
        reservations = tuple(
            view
            for view in await self._active_line_reservations(
                tenant_id=tenant_id,
                order_reference=order_reference,
                line_references=affected,
                session=session,
            )
            if view.case_id == case_id
        )
        revision = None if case is None else case.get("version")
        return SelectionOutcome(
            case_id=case_id,
            revision=revision if isinstance(revision, int) else 0,
            changed=changed,
            items=tuple(items),
            reservations=reservations,
        )

    async def _write_reservations(
        self,
        session: AsyncClientSession,
        *,
        case_id: str,
        tenant_id: str,
        order_reference: str,
        requested: Mapping[str, LineSelection],
        held: Mapping[str, ReservationView],
        now: datetime,
        ttl_seconds: int,
    ) -> bool:
        """Release what changed, hold what is new. Returns whether anything moved.

        A hold whose quantity is unchanged is left exactly as it is, deadline
        included. Refreshing the TTL on an identical re-submit would make a
        polling client able to keep a hold alive forever without an associate
        ever touching it.
        """
        changed = False
        for line, view in held.items():
            selection = requested.get(line)
            if selection is not None and selection.quantity == view.quantity:
                continue
            reason = (
                ReservationRelease.SELECTION_CHANGED
                if selection is not None
                else ReservationRelease.SELECTION_WITHDRAWN
            )
            released = await self._order_line_reservations.update_one(
                {"reservationId": view.reservation_id, "state": ReservationState.ACTIVE.value},
                {
                    "$set": {
                        "state": ReservationState.RELEASED.value,
                        "releaseReason": reason.value,
                        "updatedAt": now,
                    }
                },
                session=session,
            )
            changed = changed or released.modified_count == 1

        for line, selection in requested.items():
            existing = held.get(line)
            if existing is not None and existing.quantity == selection.quantity:
                continue
            await self._order_line_reservations.insert_one(
                new_reservation_document(
                    reservation_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    case_id=case_id,
                    order_reference=order_reference,
                    order_line_reference=line,
                    quantity=selection.quantity,
                    now=now,
                    ttl_seconds=ttl_seconds,
                ),
                session=session,
            )
            changed = True
        return changed

    async def _write_selected_items(
        self,
        session: AsyncClientSession,
        *,
        case_id: str,
        requested: Mapping[str, LineSelection],
        current: Mapping[str, dict[str, Any]],
        now: datetime,
    ) -> bool:
        """The `selectedItems` half of the write. Returns whether anything moved.

        Items already attached to a return record are never touched: they belong
        to the RMA that covers them, and an associate editing their selection is
        not editing what Support authorized. A requested line in that state has
        already been refused above.
        """
        changed = False
        for line, document in current.items():
            if isinstance(document.get("returnRecordId"), str):
                continue
            if line in requested:
                continue
            deleted = await self.return_items.delete_one(
                {"returnItemId": document["returnItemId"], "returnRecordId": None},
                session=session,
            )
            changed = changed or deleted.deleted_count == 1

        for line, selection in requested.items():
            fields = {
                "productReference": selection.product_reference,
                "quantity": selection.quantity,
                "reason": selection.reason,
                "condition": selection.condition,
                "packageReference": selection.package_reference,
            }
            existing_item = current.get(line)
            if existing_item is None:
                await self.return_items.insert_one(
                    {
                        "returnItemId": str(uuid.uuid4()),
                        "caseId": case_id,
                        "returnRecordId": None,
                        "orderLineId": line,
                        **fields,
                        "version": 0,
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    session=session,
                )
                changed = True
                continue
            if all(existing_item.get(key) == value for key, value in fields.items()):
                continue
            await self.return_items.update_one(
                {"returnItemId": existing_item["returnItemId"]},
                {"$set": {**fields, "updatedAt": now}, "$inc": {"version": 1}},
                session=session,
            )
            changed = True
        return changed

    async def authorize_reserved_line(
        self,
        *,
        case_id: str,
        order_line_reference: str,
        return_record_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Consume the hold and attach the item to its RMA, in one transaction.

        Plan sect. 12.3's conditional consume, and the mutation it must be
        atomic with:

        ```sql
        UPDATE reservation SET state = CONSUMED
         WHERE reservationId = ? AND state = ACTIVE AND expiresAt > now
        ```

        **Losing this race must not authorize.** The expiry sweep and a
        competing authorization both filter on the same conditions, so exactly
        one of them can match; the loser matches nothing and gets
        `QuantityReservationExpiredError`, and because the assignment shares the
        transaction, the item stays unattached and no RMA is recorded against a
        quantity nobody is holding. A best-effort pair of writes here is what
        would let the total authorized exceed the quantity ordered.

        The case revision moves with the assignment: `selectedItems` loses a row
        and `returnRecords[].approvedItems` gains one, which is a projection
        change a polling client must see.
        """
        moment = now or utc_now()

        async def _write(session: AsyncClientSession) -> dict[str, Any]:
            consumed = await self._order_line_reservations.find_one_and_update(
                {
                    "caseId": case_id,
                    "orderLineReference": order_line_reference,
                    "state": ReservationState.ACTIVE.value,
                    "expiresAt": {"$gt": moment},
                },
                {
                    "$set": {
                        "state": ReservationState.CONSUMED.value,
                        "consumedByReturnRecordId": return_record_id,
                        "updatedAt": moment,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if consumed is None:
                raise await self._reservation_lost(
                    case_id=case_id,
                    order_line_reference=order_line_reference,
                    session=session,
                )
            item = await self.return_items.find_one_and_update(
                {"caseId": case_id, "orderLineId": order_line_reference, "returnRecordId": None},
                {
                    "$set": {"returnRecordId": return_record_id, "updatedAt": moment},
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if item is None:
                # The hold existed and the item it stood for did not. Raised
                # from inside the transaction so the consume rolls back with it:
                # a CONSUMED reservation with nothing attached to an RMA would
                # hold quantity that no return will ever account for.
                raise KeyError(f"{case_id}:{order_line_reference}")
            await self.bump_case_revision(case_id, session=session, when=moment)
            return cast(dict[str, Any], item)

        return await self._in_transaction(_write)

    async def _reservation_lost(
        self,
        *,
        case_id: str,
        order_line_reference: str,
        session: AsyncClientSession,
    ) -> QuantityReservationExpiredError:
        """The error for a consume that matched nothing, naming what it found."""
        current = await self._order_line_reservations.find_one(
            {"caseId": case_id, "orderLineReference": order_line_reference},
            sort=[("createdAt", DESCENDING)],
            session=session,
        )
        view = None if current is None else read_reservation(current)
        return QuantityReservationExpiredError(
            f"{case_id}:{order_line_reference}",
            state=None if view is None else view.state,
        )

    async def release_case_reservations(
        self,
        case_id: str,
        *,
        reason: ReservationRelease = ReservationRelease.CASE_CLOSED,
        now: datetime | None = None,
    ) -> int:
        """Give back every hold this case still has. Returns how many moved.

        Called when a case is cancelled, rejected or expires. **No revision
        bump**: no field of the `CaseDetail` projection carries a reservation,
        so nothing a client renders has changed, and bumping would invalidate
        every cached projection for a bookkeeping write. The status change that
        caused the release is what the client sees, and that writer bumps.

        Conditional on `ACTIVE`, so a hold an RMA already consumed is left
        alone -- `CONSUMED -> RELEASED` is not an edge of the state machine, and
        giving the quantity back after it was authorized would let the line be
        sold twice.
        """
        moment = now or utc_now()
        result = await self._order_line_reservations.update_many(
            {"caseId": case_id, "state": ReservationState.ACTIVE.value},
            {
                "$set": {
                    "state": ReservationState.RELEASED.value,
                    "releaseReason": reason.value,
                    "updatedAt": moment,
                }
            },
        )
        return int(result.modified_count)

    async def count_due_reservations(self, *, now: datetime | None = None, limit: int = 200) -> int:
        """How many holds are past their deadline and still `ACTIVE`, up to `limit`.

        The sweep's candidate count, read on the same predicate and the same
        index (`reservation_expiry_sweep`) the sweep itself uses, so a reclaimer
        can report "examined" honestly instead of inferring it from how many it
        managed to move. Bounded by `limit` for the reason the sweep is bounded:
        a backlog is counted up to the batch a pass would attempt, never scanned
        in full to produce a number nobody acts on.
        """
        moment = now or utc_now()
        return int(
            await self._order_line_reservations.count_documents(
                {"state": ReservationState.ACTIVE.value, "expiresAt": {"$lte": moment}},
                limit=max(1, limit),
            )
        )

    async def expire_due_reservations(
        self, *, now: datetime | None = None, limit: int = 200
    ) -> int:
        """Settle holds whose TTL has elapsed. Returns how many moved.

        **The arithmetic does not wait for this.** `is_held` already treats an
        `ACTIVE` reservation past its deadline as holding nothing, so an
        abandoned selection releases its quantity the moment the deadline
        passes whether or not any sweep runs. This exists to settle the record
        -- so the audit trail says `EXPIRED` rather than leaving an `ACTIVE`
        document that stopped meaning anything -- and to keep the partial unique
        index from refusing a fresh hold on a line the same case abandoned.

        One conditional update per document rather than a single `update_many`:
        the predicate has to be re-checked at the moment of the write, because
        an authorization may be consuming the very reservation this sweep
        selected. Each loser matches nothing and is simply not counted, which is
        the same rule the authorization plays by from the other side.
        """
        moment = now or utc_now()
        cursor = (
            self._order_line_reservations.find(
                {"state": ReservationState.ACTIVE.value, "expiresAt": {"$lte": moment}},
                {"reservationId": 1},
            )
            .sort("expiresAt", ASCENDING)
            .limit(limit)
        )
        expired = 0
        async for document in cursor:
            reservation_id = document.get("reservationId")
            if not isinstance(reservation_id, str):
                continue
            result = await self._order_line_reservations.update_one(
                {
                    "reservationId": reservation_id,
                    "state": ReservationState.ACTIVE.value,
                    "expiresAt": {"$lte": moment},
                },
                {"$set": {"state": ReservationState.EXPIRED.value, "updatedAt": moment}},
            )
            expired += int(result.modified_count)
        return expired

    # ------------------------------------------------------------------
    # Migration and backfill (plan sect. 6.7)
    # ------------------------------------------------------------------

    async def backfill_case(self, case_id: str, *, workflow_terminated: bool) -> CaseBackfillPlan:
        """Bring one existing case up to what the projection needs. Safe to re-run.

        Returns the plan that was applied, which is `is_noop` for a case that
        needed nothing -- and is `is_noop` on every run after the first, because
        `plan_case_backfill` reads the document rather than remembering what it
        did. That is what makes idempotence testable: the second run produces an
        empty plan, not equal writes.

        `workflow_terminated` is the caller's answer, not a lookup. The six
        orphaned cases are orphaned precisely because their durable execution
        terminated, and whoever runs the migration is the one holding a Temporal
        client -- a repository that reached for one would make the workflow host
        a dependency of the case aggregate.

        Never destructive. It sets a missing revision and it recovers an orphan;
        it deletes nothing, and it rewrites no value a writer already chose.
        """
        case = await self.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        plan = plan_case_backfill(case, workflow_terminated=workflow_terminated)
        if plan.is_noop:
            return plan

        if CaseBackfillAction.INITIALIZE_REVISION in plan.actions:
            # `$exists: false` rather than an expected version: Mongo does not
            # match a missing field against `0`, so `update_case` cannot reach
            # these documents at all -- and the filter is what makes a second
            # run match nothing instead of resetting a revision that has since
            # advanced. `updatedAt` is untouched on purpose; the projection
            # already reads a missing version as `0`, so nothing observable
            # changes and a recency sort must not be disturbed by a migration.
            await self.cases.update_one(
                {"caseId": plan.case_id, "version": {"$exists": False}},
                {"$set": {"version": plan.expected_version}},
            )

        if plan.status is not None:
            # Through `update_case`, so the revision bump and `updatedAt` happen
            # in the same write as the status. This one *does* change the
            # projection, and plan sect. 6.5 admits no best-effort second write.
            await self.update_case(
                plan.case_id,
                {"status": plan.status.value},
                expected_version=plan.expected_version,
            )
        return plan

    async def backfill_cases(
        self,
        case_ids: Iterable[str],
        *,
        terminated_case_ids: Collection[str] = (),
    ) -> tuple[CaseBackfillPlan, ...]:
        """Backfill a named set of cases, in the order given.

        The caller names the cases and names which of them have a terminated
        execution. Deliberately not a collection scan: a migration that decided
        for itself which documents to touch would be a migration nobody could
        rehearse against a list first.
        """
        return tuple(
            [
                await self.backfill_case(
                    case_id, workflow_terminated=case_id in terminated_case_ids
                )
                for case_id in case_ids
            ]
        )
