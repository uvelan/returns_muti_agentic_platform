"""Immutable, content-addressed capture of what a set of sources looks like.

A snapshot is the analyzer's evidence base: every schema proposal the reasoning
layer later makes must be attributable to one. It is therefore immutable and
identified by a hash of its own metadata, so a proposal can never silently be
re-grounded against different evidence than it was produced from.

The sensitive/non-sensitive split is a hard contract (design doc section 13.6),
not a convention: **dataset metadata is always plaintext and always retained**;
**samples are never persisted unclassified**. `SampleClassification` records
which of the three permitted dispositions was applied, and
`SourceSchemaSnapshot` refuses to be constructed in a state that contradicts it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "DatasetMetadata",
    "FieldMetadata",
    "SampleClassification",
    "SourceSchemaSnapshot",
    "content_hash_of",
]


class SampleClassification(StrEnum):
    """How this snapshot's source samples were dispositioned before durable write.

    NONE      metadata-only; samples were used transiently in the AI call and
              never written. No samples_ref.
    REDACTED  samples persisted only after the platform redactor. The default
              whenever sampling is enabled.
    ENCRYPTED raw samples persisted encrypted in `source_samples`, with a
              mandatory expiry. Requires the source definition to opt in.
    """

    NONE = "NONE"
    REDACTED = "REDACTED"
    ENCRYPTED = "ENCRYPTED"


class FieldMetadata(BaseModel):
    """One field's shape. Metadata only -- never a value drawn from the source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str
    declared_type: str
    nullable: bool = True
    distinct_count: int | None = None
    null_count: int | None = None


class DatasetMetadata(BaseModel):
    """One collection/table as discovered. Always plaintext, always retained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    dataset_name: str
    fields: tuple[FieldMetadata, ...]
    approximate_row_count: int | None = None


def content_hash_of(datasets: tuple[DatasetMetadata, ...]) -> str:
    """Content address for a snapshot: a digest over canonicalized dataset
    metadata only.

    Samples are excluded on purpose. Two captures of the same source shape must
    produce the same hash regardless of which rows happened to be sampled, or the
    address would be useless for recognising "the schema has not changed". Field
    order within a dataset is preserved (it is part of the discovered shape), but
    datasets are sorted so discovery order cannot change the identity.

    Deliberately not `dynamic_knowledge.fingerprint.sha256_digest`: that lives in
    another business module, and this module imports none (section 2.7). It is a
    handful of lines, and duplicating them is the cheaper of the two costs.
    """
    payload = [
        dataset.model_dump(mode="json")
        for dataset in sorted(datasets, key=lambda item: (item.source_id, item.dataset_name))
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SourceSchemaSnapshot(BaseModel):
    """One immutable capture of source shape for one analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    analysis_id: str
    content_hash: str
    datasets: tuple[DatasetMetadata, ...]
    sample_classification: SampleClassification
    samples_ref: str | None = None
    sample_expires_at: datetime | None = None
    captured_at: datetime

    @model_validator(mode="after")
    def _classification_matches_payload(self) -> SourceSchemaSnapshot:
        """Fail closed on any combination section 13.6 does not permit.

        Enforced here, in the domain object's constructor, rather than in the
        repository: a snapshot that misrepresents how its samples were handled
        must be impossible to *hold*, not merely impossible to save. There is no
        code path that can build one and decide later.
        """
        from return_platform.graph_schema_analyzer.domain.errors import ClassificationViolation

        if self.sample_classification is SampleClassification.NONE:
            if self.samples_ref is not None or self.sample_expires_at is not None:
                raise ClassificationViolation(
                    "sample_classification=NONE means no samples were persisted, so "
                    "samples_ref and sample_expires_at must both be unset."
                )
        else:
            if self.samples_ref is None:
                raise ClassificationViolation(
                    f"sample_classification={self.sample_classification} claims samples "
                    "were persisted, but samples_ref is unset."
                )
        if self.sample_classification is SampleClassification.ENCRYPTED:
            # Section 13.6 makes the TTL mandatory for raw retained samples --
            # an encrypted sample with no expiry is an indefinite plaintext-
            # equivalent liability the moment the key is ever compromised.
            if self.sample_expires_at is None:
                raise ClassificationViolation(
                    "sample_classification=ENCRYPTED requires a sample_expires_at; raw "
                    "samples may never be retained indefinitely."
                )
        return self

    @model_validator(mode="after")
    def _content_hash_is_self_consistent(self) -> SourceSchemaSnapshot:
        expected = content_hash_of(self.datasets)
        if self.content_hash != expected:
            from return_platform.graph_schema_analyzer.domain.errors import SnapshotIntegrityError

            raise SnapshotIntegrityError(
                f"snapshot {self.snapshot_id} declares content_hash {self.content_hash} "
                f"but its datasets hash to {expected}."
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        analysis_id: str,
        datasets: tuple[DatasetMetadata, ...],
        sample_classification: SampleClassification,
        captured_at: datetime,
        samples_ref: str | None = None,
        sample_expires_at: datetime | None = None,
    ) -> SourceSchemaSnapshot:
        """Build a snapshot with its content address derived, never supplied."""
        return cls(
            snapshot_id=snapshot_id,
            analysis_id=analysis_id,
            content_hash=content_hash_of(datasets),
            datasets=datasets,
            sample_classification=sample_classification,
            samples_ref=samples_ref,
            sample_expires_at=sample_expires_at,
            captured_at=captured_at,
        )

    def describes_same_shape_as(self, other: SourceSchemaSnapshot) -> bool:
        """Whether two captures found identical source shape -- the question
        'do we need to re-reason about this?' reduces to comparing addresses."""
        return self.content_hash == other.content_hash

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
