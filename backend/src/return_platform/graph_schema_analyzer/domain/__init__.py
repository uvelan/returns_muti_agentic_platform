"""Analyzer domain types: pure, immutable, and dependency-free.

Nothing in this package performs I/O or imports a port -- the invariants here
(session transitions, sample classification, content addressing) hold regardless
of where the data came from or where it is going.
"""

from __future__ import annotations

from return_platform.graph_schema_analyzer.domain.analysis_session import (
    AnalysisSession,
    SessionStatus,
)
from return_platform.graph_schema_analyzer.domain.clarification import (
    Clarification,
    ClarificationStatus,
)
from return_platform.graph_schema_analyzer.domain.errors import (
    AnalyzerError,
    ClassificationViolation,
    ConcurrentModification,
    InvalidSessionTransition,
    SnapshotIntegrityError,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
    content_hash_of,
)

__all__ = [
    "AnalysisSession",
    "AnalyzerError",
    "Clarification",
    "ClarificationStatus",
    "ClassificationViolation",
    "ConcurrentModification",
    "DatasetMetadata",
    "FieldMetadata",
    "InvalidSessionTransition",
    "SampleClassification",
    "SessionStatus",
    "SnapshotIntegrityError",
    "SourceSchemaSnapshot",
    "UnknownAnalysis",
    "content_hash_of",
]
