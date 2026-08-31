"""S1 -- loose-artifact binding rules and persistence (contracts.md sect. 4).

Every branch of the contract's decision table is here, plus the two
properties the persistence half must not lose: bound artifacts merge under
`RETURN_RECORD_MERGED_FIELDS` semantics (null never overwrites, redelivery
writes nothing), and AMBIGUOUS / UNMATCHED artifacts never touch a record --
least of all by creating one.
"""

from typing import Any

import pytest

from return_platform.operations.artifact_binding import (
    ARTIFACT_STORED_FIELDS,
    ArtifactBindingDecision,
    ArtifactType,
    BindingStatus,
    ExtractedArtifact,
    bind_artifact,
    bind_artifacts,
    persist_binding_decision,
)
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.fact_names import (
    SUPPORT_ARTIFACT_AMBIGUOUS,
    SUPPORT_ARTIFACT_UNMATCHED,
)


def _record(record_id: str, reference: str | None, **fields: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "returnRecordId": record_id,
        "caseId": "case-1",
        "returnReference": reference,
        "status": "ISSUED",
        "returnLocation": None,
        "trackingReference": None,
        "labelReference": None,
        "shippingInstructionReference": None,
        "version": 0,
    }
    document.update(fields)
    return document


def _tracking(value: str = "TRK-1", binding: str | None = None) -> ExtractedArtifact:
    return ExtractedArtifact(artifact_type=ArtifactType.TRACKING, value=value, binding=binding)


# ---------------------------------------------------------------------------
# The pure rules
# ---------------------------------------------------------------------------


class TestBindingRules:
    def test_an_artifact_naming_a_known_reference_binds_to_that_record(self) -> None:
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        decision = bind_artifact(_tracking(binding="RMA-2"), records)
        assert decision.status is BindingStatus.BOUND
        assert decision.return_record_id == "rec-2"

    def test_an_rma_artifact_names_itself(self) -> None:
        """Its value *is* a return reference; the no-reference rules can never
        apply to it."""
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        artifact = ExtractedArtifact(artifact_type=ArtifactType.RMA, value="RMA-1")
        decision = bind_artifact(artifact, records)
        assert decision.status is BindingStatus.BOUND
        assert decision.return_record_id == "rec-1"

    def test_an_unknown_reference_is_unmatched_and_never_a_new_record(self) -> None:
        decision = bind_artifact(_tracking(binding="RMA-9"), [_record("rec-1", "RMA-1")])
        assert decision.status is BindingStatus.UNMATCHED
        assert decision.return_record_id is None
        assert "RMA-9" in (decision.reason or "")

    def test_no_reference_and_exactly_one_record_binds(self) -> None:
        decision = bind_artifact(_tracking(), [_record("rec-1", "RMA-1")])
        assert decision.status is BindingStatus.BOUND
        assert decision.return_record_id == "rec-1"

    def test_no_reference_and_several_records_is_ambiguous(self) -> None:
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        decision = bind_artifact(_tracking(), records)
        assert decision.status is BindingStatus.AMBIGUOUS
        assert decision.candidate_record_ids == ("rec-1", "rec-2")

    def test_no_reference_and_no_records_is_unmatched(self) -> None:
        """The corner the contract leaves open, closed conservatively: nothing
        to bind to, nothing to disambiguate between, and creating a record is
        forbidden."""
        decision = bind_artifact(_tracking(), [])
        assert decision.status is BindingStatus.UNMATCHED

    def test_a_blank_binding_claim_is_no_reference(self) -> None:
        decision = bind_artifact(_tracking(binding="  "), [_record("rec-1", "RMA-1")])
        assert decision.status is BindingStatus.BOUND
        assert decision.return_record_id == "rec-1"

    def test_bind_artifacts_decides_each_against_one_read(self) -> None:
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        decisions = bind_artifacts([_tracking(binding="RMA-1"), _tracking("TRK-2")], records)
        assert [decision.status for decision in decisions] == [
            BindingStatus.BOUND,
            BindingStatus.AMBIGUOUS,
        ]


class TestStoredFieldMirror:
    def test_the_field_map_cannot_drift_from_return_record_merged_fields(self) -> None:
        """`ARTIFACT_STORED_FIELDS` mirrors the workflow layer's merge table
        rather than importing it (the import would point the wrong way); this
        pin is what makes the mirror safe."""
        from return_platform.workflows.return_case_activities import (
            RETURN_RECORD_MERGED_FIELDS,
        )

        merged_stored_keys = {stored_key for stored_key, _, _ in RETURN_RECORD_MERGED_FIELDS}
        mirrored = {key for key in ARTIFACT_STORED_FIELDS.values() if key is not None}
        assert mirrored <= merged_stored_keys


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class _RecordStore:
    """`list_return_records` / `update_return_record` with the shipped
    semantics: optimistic version, loser raises, no create path at all."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.conflicts_remaining = 0

    async def list_return_records(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(record) for record in self.records if record["caseId"] == case_id]

    async def update_return_record(
        self, return_record_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        stored = next(
            record for record in self.records if record["returnRecordId"] == return_record_id
        )
        if self.conflicts_remaining > 0:
            self.conflicts_remaining -= 1
            stored["version"] = int(stored["version"]) + 1
            raise ConcurrencyConflictError(return_record_id)
        if expected_version != stored["version"]:
            raise ConcurrencyConflictError(return_record_id)
        stored.update(updates)
        stored["version"] = int(stored["version"]) + 1
        self.updates.append((return_record_id, dict(updates)))
        return dict(stored)


class _FactAppender:
    """`append_scoped_fact_once`'s contract: append-once on the derived id."""

    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    async def __call__(self, *, record_scope: str | None, **fact: Any) -> bool:
        if any(held["fact_id"] == fact["fact_id"] for held in self.appended):
            return False
        self.appended.append({"record_scope": record_scope, **fact})
        return True


def _bound(artifact: ExtractedArtifact, record_id: str) -> ArtifactBindingDecision:
    return ArtifactBindingDecision(
        artifact=artifact, status=BindingStatus.BOUND, return_record_id=record_id
    )


async def _persist(
    decision: ArtifactBindingDecision,
    store: _RecordStore,
    appender: _FactAppender | None = None,
    dedupe_key: str = "evt-1-0",
) -> bool:
    return await persist_binding_decision(
        decision,
        case_id="case-1",
        dedupe_key=dedupe_key,
        records=store,
        append_scoped_fact_once=appender or _FactAppender(),
    )


@pytest.mark.asyncio
class TestBoundPersistence:
    async def test_a_bound_tracking_number_merges_onto_its_record(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1", labelReference="LBL-1")])
        assert await _persist(_bound(_tracking("TRK-9"), "rec-1"), store)
        record_id, updates = store.updates[0]
        assert record_id == "rec-1"
        # This field because the artifact gave one -- and *only* this field,
        # which is what makes a null unable to overwrite: the label the
        # record already holds is not in the write at all.
        assert updates == {"trackingReference": "TRK-9"}
        assert store.records[0]["labelReference"] == "LBL-1"

    async def test_a_redelivered_value_writes_nothing(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1", trackingReference="TRK-9")])
        assert not await _persist(_bound(_tracking("TRK-9"), "rec-1"), store)
        assert store.updates == []

    async def test_a_blank_artifact_value_is_the_absence_of_a_statement(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1", trackingReference="TRK-9")])
        assert not await _persist(_bound(_tracking("  "), "rec-1"), store)
        assert store.updates == []
        assert store.records[0]["trackingReference"] == "TRK-9"

    async def test_a_bound_rma_artifact_confirms_identity_and_merges_nothing(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1")])
        artifact = ExtractedArtifact(artifact_type=ArtifactType.RMA, value="RMA-1")
        assert not await _persist(_bound(artifact, "rec-1"), store)
        assert store.updates == []

    async def test_a_version_conflict_is_retried_once_from_a_re_read(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1")])
        store.conflicts_remaining = 1
        assert await _persist(_bound(_tracking("TRK-9"), "rec-1"), store)
        assert store.records[0]["trackingReference"] == "TRK-9"

    async def test_a_second_conflict_propagates_to_the_caller(self) -> None:
        store = _RecordStore([_record("rec-1", "RMA-1")])
        store.conflicts_remaining = 2
        with pytest.raises(ConcurrencyConflictError):
            await _persist(_bound(_tracking("TRK-9"), "rec-1"), store)

    # -- the record-*selection* step, on a case that actually holds a choice --
    #
    # Every test above this line stores exactly one record, so `records[0]` and
    # "the record the decision names" are the same document and the search in
    # `_merge_bound_artifact` cannot be wrong. On a multi-RMA case they come
    # apart, and that is the only shape in which cross-assignment -- Support's
    # tracking for RMA-2 landing on RMA-1 -- is expressible at all. (ACC3
    # category-B audit: the two below are the tests INJ-B4 found missing.)

    async def test_a_bound_artifact_merges_onto_the_named_record_not_the_first(
        self,
    ) -> None:
        """Item 8's cross-assignment case, at the persistence layer.

        The decision names the *second* record. Asserting only that rec-2 was
        written would still pass if rec-1 were written too, so the untouched
        neighbour is asserted as well: a customer's tracking number appearing
        on someone else's return is the business failure being excluded.
        """
        store = _RecordStore(
            [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        )
        assert await _persist(_bound(_tracking("TRK-9"), "rec-2"), store)
        assert store.updates == [("rec-2", {"trackingReference": "TRK-9"})]
        assert store.records[0]["trackingReference"] is None, (
            "the first record is not the bound one and must be untouched"
        )
        assert store.records[1]["trackingReference"] == "TRK-9"

    async def test_a_decision_naming_a_record_the_case_does_not_hold_refuses(
        self,
    ) -> None:
        """The merge refuses rather than falling back to a neighbour.

        With one stored record this branch is unreachable-by-accident: any
        fallback would pick the record the decision meant anyway. With two, a
        silent fallback is a mis-assignment, so the raise is load-bearing.
        """
        store = _RecordStore(
            [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        )
        with pytest.raises(LookupError):
            await _persist(_bound(_tracking("TRK-9"), "rec-404"), store)
        assert store.updates == []


@pytest.mark.asyncio
class TestUnboundPersistence:
    async def test_an_ambiguous_artifact_writes_its_fact_and_no_record(self) -> None:
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        store = _RecordStore(records)
        appender = _FactAppender()
        decision = bind_artifact(_tracking("TRK-9"), records)
        assert await _persist(decision, store, appender)

        assert store.updates == []
        (fact,) = appender.appended
        assert fact["fact_name"] == SUPPORT_ARTIFACT_AMBIGUOUS
        assert fact["record_scope"] is None
        assert fact["value"]["candidateRecordIds"] == ["rec-1", "rec-2"]
        assert fact["value"]["value"] == "TRK-9"

    async def test_an_unmatched_artifact_writes_its_fact_and_no_record(self) -> None:
        records = [_record("rec-1", "RMA-1")]
        store = _RecordStore(records)
        appender = _FactAppender()
        decision = bind_artifact(_tracking("TRK-9", binding="RMA-9"), records)
        assert await _persist(decision, store, appender)

        assert store.updates == []
        (fact,) = appender.appended
        assert fact["fact_name"] == SUPPORT_ARTIFACT_UNMATCHED
        assert fact["value"]["namedReference"] == "RMA-9"
        assert fact["value"]["reason"]

    async def test_a_redelivered_message_dedupes_on_the_key(self) -> None:
        records = [_record("rec-1", "RMA-1"), _record("rec-2", "RMA-2")]
        appender = _FactAppender()
        decision = bind_artifact(_tracking("TRK-9"), records)
        assert await _persist(decision, _RecordStore(records), appender, dedupe_key="evt-7-0")
        assert not await _persist(decision, _RecordStore(records), appender, dedupe_key="evt-7-0")
        assert len(appender.appended) == 1
