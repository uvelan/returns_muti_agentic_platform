"""Typed failures for the Graph Schema Analyzer.

Deliberately module-local rather than reusing another module's error hierarchy:
`graph_schema_analyzer` imports no other business module (design doc section 2.7),
and an exception type is as much a coupling as a service type.
"""

from __future__ import annotations

__all__ = [
    "AnalyzerError",
    "ClassificationViolation",
    "ConcurrentModification",
    "InvalidSessionTransition",
    "SnapshotIntegrityError",
    "UnknownAnalysis",
]


class AnalyzerError(Exception):
    """Base for every analyzer-owned failure."""


class UnknownAnalysis(AnalyzerError):
    """No analysis session exists for the requested id."""


class InvalidSessionTransition(AnalyzerError):
    """A session status change that the lifecycle does not permit."""


class ConcurrentModification(AnalyzerError):
    """An optimistic-concurrency write lost: the stored `version` moved underneath
    this caller. Never retried silently -- the caller must re-read and re-decide,
    because a blind retry would overwrite whatever the winner just wrote."""


class ClassificationViolation(AnalyzerError):
    """A snapshot's sample payload does not match its declared
    `sample_classification` (design doc section 13.6) -- e.g. NONE carrying a
    samples reference, or ENCRYPTED without the mandatory expiry. Raised before
    any durable write, never after."""


class SnapshotIntegrityError(AnalyzerError):
    """A stored snapshot's recomputed content hash does not match the persisted
    one, so the metadata was mutated after capture. A snapshot is immutable and
    content-addressed by contract; this means the record is not trustworthy."""
