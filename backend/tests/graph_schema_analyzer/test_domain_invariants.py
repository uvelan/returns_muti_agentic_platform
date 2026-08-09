"""Domain invariants that other layers are allowed to depend on.

Pure tests -- no infra. The point of each is that a *later* layer (persistence,
API, the C3.2 reasoning loop) can assume these hold without re-checking them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from return_platform.graph_schema_analyzer.domain import (
    AnalysisSession,
    Clarification,
    ClarificationStatus,
    ClassificationViolation,
    DatasetMetadata,
    FieldMetadata,
    InvalidSessionTransition,
    SampleClassification,
    SessionStatus,
    SnapshotIntegrityError,
    SourceSchemaSnapshot,
    content_hash_of,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _datasets(*, field_name: str = "order_id") -> tuple[DatasetMetadata, ...]:
    return (
        DatasetMetadata(
            source_id="mongo_main",
            dataset_name="orders",
            fields=(FieldMetadata(field_name=field_name, declared_type="string"),),
        ),
    )


def _session(status: SessionStatus = SessionStatus.DRAFT) -> AnalysisSession:
    return AnalysisSession(
        analysis_id="a1",
        status=status,
        source_refs=("mongo_main",),
        created_by="analyst@example.com",
        created_at=NOW,
        updated_at=NOW,
    )


# --- session lifecycle ------------------------------------------------------


def test_illegal_transition_is_rejected() -> None:
    """DRAFT -> APPROVED would skip discovery, analysis, and validation entirely."""
    with pytest.raises(InvalidSessionTransition):
        _session().transitioned_to(SessionStatus.APPROVED, occurred_at=NOW)


def test_legal_transition_bumps_version_for_compare_and_set() -> None:
    moved = _session().transitioned_to(SessionStatus.DISCOVERING, occurred_at=NOW)
    assert moved.status is SessionStatus.DISCOVERING
    assert moved.version == 1


def test_terminal_status_permits_nothing_further() -> None:
    approved = _session(SessionStatus.READY_FOR_APPROVAL).transitioned_to(
        SessionStatus.APPROVED, occurred_at=NOW
    )
    assert approved.is_terminal
    with pytest.raises(InvalidSessionTransition):
        approved.transitioned_to(SessionStatus.ANALYZING, occurred_at=NOW)


def test_editing_a_ready_schema_returns_it_to_analyzing() -> None:
    """Any mutation invalidates the validation result that made it ready."""
    ready = _session(SessionStatus.READY_FOR_APPROVAL)
    assert ready.transitioned_to(SessionStatus.ANALYZING, occurred_at=NOW).status is (
        SessionStatus.ANALYZING
    )


# --- sample classification (design doc 13.6) --------------------------------


def test_none_classification_cannot_carry_a_samples_reference() -> None:
    with pytest.raises(ClassificationViolation):
        SourceSchemaSnapshot.create(
            snapshot_id="s1",
            analysis_id="a1",
            datasets=_datasets(),
            sample_classification=SampleClassification.NONE,
            captured_at=NOW,
            samples_ref="samples-1",
        )


def test_redacted_classification_requires_a_samples_reference() -> None:
    with pytest.raises(ClassificationViolation):
        SourceSchemaSnapshot.create(
            snapshot_id="s1",
            analysis_id="a1",
            datasets=_datasets(),
            sample_classification=SampleClassification.REDACTED,
            captured_at=NOW,
        )


def test_encrypted_samples_may_never_be_retained_without_an_expiry() -> None:
    """Raw samples with no TTL are an indefinite liability the moment a key leaks."""
    with pytest.raises(ClassificationViolation):
        SourceSchemaSnapshot.create(
            snapshot_id="s1",
            analysis_id="a1",
            datasets=_datasets(),
            sample_classification=SampleClassification.ENCRYPTED,
            captured_at=NOW,
            samples_ref="samples-1",
        )


def test_encrypted_with_expiry_is_accepted() -> None:
    snapshot = SourceSchemaSnapshot.create(
        snapshot_id="s1",
        analysis_id="a1",
        datasets=_datasets(),
        sample_classification=SampleClassification.ENCRYPTED,
        captured_at=NOW,
        samples_ref="samples-1",
        sample_expires_at=NOW + timedelta(days=7),
    )
    assert snapshot.sample_classification is SampleClassification.ENCRYPTED


# --- content addressing -----------------------------------------------------


def test_identical_shape_produces_an_identical_address() -> None:
    left = SourceSchemaSnapshot.create(
        snapshot_id="s1",
        analysis_id="a1",
        datasets=_datasets(),
        sample_classification=SampleClassification.NONE,
        captured_at=NOW,
    )
    right = SourceSchemaSnapshot.create(
        snapshot_id="s2",
        analysis_id="a1",
        datasets=_datasets(),
        sample_classification=SampleClassification.NONE,
        captured_at=NOW + timedelta(hours=1),
    )
    assert left.describes_same_shape_as(right)


def test_a_changed_field_changes_the_address() -> None:
    assert content_hash_of(_datasets()) != content_hash_of(_datasets(field_name="renamed"))


def test_discovery_order_does_not_affect_the_address() -> None:
    """Two sources discovered in either order describe the same shape."""
    a = DatasetMetadata(source_id="s_a", dataset_name="t", fields=())
    b = DatasetMetadata(source_id="s_b", dataset_name="t", fields=())
    assert content_hash_of((a, b)) == content_hash_of((b, a))


def test_tampered_metadata_fails_to_load() -> None:
    """A snapshot whose stored hash no longer matches its datasets is not
    trustworthy evidence, so reconstructing it raises instead of returning."""
    snapshot = SourceSchemaSnapshot.create(
        snapshot_id="s1",
        analysis_id="a1",
        datasets=_datasets(),
        sample_classification=SampleClassification.NONE,
        captured_at=NOW,
    )
    document = dict(snapshot.to_document())
    document["datasets"] = [
        dict(
            DatasetMetadata(source_id="mongo_main", dataset_name="injected", fields=()).model_dump(
                mode="json"
            )
        )
    ]
    with pytest.raises(SnapshotIntegrityError):
        SourceSchemaSnapshot.model_validate(document)


# --- clarifications ---------------------------------------------------------


def test_a_clarification_cannot_be_answered_twice() -> None:
    clarification = Clarification(
        clarification_id="c1", analysis_id="a1", question="Which key joins these?", asked_at=NOW
    )
    answered = clarification.answered("order_id", by="analyst", occurred_at=NOW)
    assert answered.status is ClarificationStatus.ANSWERED
    with pytest.raises(InvalidSessionTransition):
        answered.answered("something else", by="analyst", occurred_at=NOW)


def test_a_withdrawn_clarification_cannot_be_resurrected_by_a_late_answer() -> None:
    clarification = Clarification(
        clarification_id="c1", analysis_id="a1", question="Which key?", asked_at=NOW
    ).withdrawn(occurred_at=NOW)
    with pytest.raises(InvalidSessionTransition):
        clarification.answered("too late", by="analyst", occurred_at=NOW)
