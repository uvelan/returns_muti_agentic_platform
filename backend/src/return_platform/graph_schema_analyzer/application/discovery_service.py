"""Resolve sources -> metadata -> bounded samples -> a classified snapshot.

This is the only place source data enters the analyzer, which makes it the only
place section 13.6 has to be enforced on the way in. The ordering below is the
whole point and is not incidental:

    read (bounded by policy)
      -> build metadata            [always plaintext, always retained]
      -> classify samples          [NONE / REDACTED / ENCRYPTED]
      -> redact if REDACTED        [mandatory pass, before anything durable]
      -> seal + write samples      [encrypted structure, mandatory TTL]
      -> build snapshot            [content-addressed over metadata only]

A snapshot is never constructed before its samples have been dispositioned, so
there is no window in which a snapshot exists claiming a classification that has
not actually been applied.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from return_platform.graph_schema_analyzer.domain.errors import ClassificationViolation
from return_platform.graph_schema_analyzer.domain.sampling_policy import SamplingPolicy
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    DiscoveredDataset,
    SourceDiscoveryPort,
)
from return_platform.platform.redaction.allowlist import AllowlistRedactor

__all__ = ["DiscoveryOutcome", "DiscoveryService"]

logger = logging.getLogger(__name__)


class DiscoveryOutcome:
    """A snapshot plus the sealed-sample payload the caller must persist.

    Returned as a pair rather than written here so the service stays free of the
    sample repository: what to *do* with retained samples is a persistence
    concern, while deciding *whether they may exist at all* is this service's.
    """

    __slots__ = ("rows_by_dataset", "samples_expire_at", "snapshot")

    def __init__(
        self,
        *,
        snapshot: SourceSchemaSnapshot,
        rows_by_dataset: Mapping[str, list[dict[str, Any]]] | None,
        samples_expire_at: datetime | None,
    ) -> None:
        self.snapshot = snapshot
        self.rows_by_dataset = rows_by_dataset
        self.samples_expire_at = samples_expire_at

    @property
    def has_samples_to_persist(self) -> bool:
        return self.rows_by_dataset is not None


class DiscoveryService:
    def __init__(self, sources: SourceDiscoveryPort) -> None:
        self._sources = sources

    async def discover(
        self,
        *,
        analysis_id: str,
        policies: Sequence[SamplingPolicy],
        captured_at: datetime,
    ) -> DiscoveryOutcome:
        """Capture one snapshot across every requested source.

        Each source carries its own policy, so a permissive source cannot widen
        access to a restrictive one in the same analysis. A single snapshot may
        therefore mix per-source access levels; its *retention* classification is
        the strictest thing it can honestly claim (see `_snapshot_classification`).
        """
        if not policies:
            raise ClassificationViolation("discovery requires at least one source policy.")

        datasets: list[DatasetMetadata] = []
        retained: dict[str, list[dict[str, Any]]] = {}

        for policy in policies:
            discovered = await self._sources.discover(
                source_id=policy.source_id, sample_limit=policy.effective_sample_limit
            )
            for dataset in discovered:
                datasets.append(_metadata_of(dataset))
                rows = self._retainable_rows(policy, dataset)
                if rows is not None:
                    retained[f"{dataset.source_id}.{dataset.dataset_name}"] = rows

        classification = _snapshot_classification(policies)
        samples_ref: str | None = None
        expires_at: datetime | None = None
        if classification is not SampleClassification.NONE:
            samples_ref = f"samples-{uuid4()}"
            expires_at = captured_at + _shortest_retention(policies)

        snapshot = SourceSchemaSnapshot.create(
            snapshot_id=f"snapshot-{uuid4()}",
            analysis_id=analysis_id,
            datasets=tuple(datasets),
            sample_classification=classification,
            captured_at=captured_at,
            samples_ref=samples_ref,
            sample_expires_at=expires_at,
        )
        logger.info(
            "analyzer_source_discovery_captured",
            extra={
                "analysis_id": analysis_id,
                "dataset_count": len(datasets),
                "sample_classification": classification.value,
                "retained_dataset_count": len(retained),
            },
        )
        return DiscoveryOutcome(
            snapshot=snapshot,
            rows_by_dataset=retained if samples_ref is not None else None,
            samples_expire_at=expires_at,
        )

    def _retainable_rows(
        self, policy: SamplingPolicy, dataset: DiscoveredDataset
    ) -> list[dict[str, Any]] | None:
        """What may be written durably for one dataset, or None for "nothing".

        Under NONE the rows discovery just read are simply dropped here -- they
        remain available to the caller's in-flight AI call through the port's
        return value, but nothing about them reaches this function's output, so
        no durable path can pick them up later.
        """
        if not policy.retains_samples:
            return None
        rows = list(dataset.sample_rows)
        if policy.retention_classification is SampleClassification.REDACTED:
            redactor = AllowlistRedactor(policy.retention_allowlist)
            return [dict(redactor.redact(row)) for row in rows]
        return [dict(row) for row in rows]


def _metadata_of(dataset: DiscoveredDataset) -> DatasetMetadata:
    """Project discovery output onto the always-plaintext metadata model.

    Field descriptors are read defensively (`.get`) because a connector's
    metadata shape varies by source type -- a missing `nullable` should degrade
    to the permissive default, not fail an entire analysis.
    """
    return DatasetMetadata(
        source_id=dataset.source_id,
        dataset_name=dataset.dataset_name,
        approximate_row_count=dataset.approximate_row_count,
        fields=tuple(
            FieldMetadata(
                field_name=str(field.get("field_name", field.get("name", "unknown"))),
                declared_type=str(field.get("declared_type", field.get("type", "unknown"))),
                nullable=bool(field.get("nullable", True)),
                distinct_count=_optional_int(field.get("distinct_count")),
                null_count=_optional_int(field.get("null_count")),
            )
            for field in dataset.fields
        ),
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _snapshot_classification(policies: Sequence[SamplingPolicy]) -> SampleClassification:
    """One snapshot carries one classification, so it must be the *weakest*
    guarantee any contributing source forced -- claiming REDACTED while one
    source contributed raw rows would be a false statement about the payload.
    """
    classifications = {policy.retention_classification for policy in policies}
    if SampleClassification.ENCRYPTED in classifications:
        return SampleClassification.ENCRYPTED
    if SampleClassification.REDACTED in classifications:
        return SampleClassification.REDACTED
    return SampleClassification.NONE


def _shortest_retention(policies: Sequence[SamplingPolicy]) -> timedelta:
    """The shortest period any retaining source asked for. Samples share one
    document, so the strictest source's expiry has to govern all of them --
    honouring the longest would silently extend another source's retention.
    """
    retaining = [p.retention_period for p in policies if p.retains_samples]
    return min(retaining) if retaining else timedelta(0)
