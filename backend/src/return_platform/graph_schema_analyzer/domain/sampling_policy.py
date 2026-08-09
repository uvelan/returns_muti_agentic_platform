"""How much source data an analysis is allowed to see, and what may be kept.

Two separate questions that are easy to conflate:

* **Access** — how many rows discovery may read at all (`max_sample_rows`, and
  `sampling_enabled` for sources where reading any row is unacceptable).
* **Retention** — what, if anything, survives the AI call
  (`retention_classification`).

They are independent. A source can permit sampling for reasoning while
permitting nothing to be written durably (`NONE`), which is the safest useful
combination and is why `NONE` is the default rather than an edge case.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from return_platform.graph_schema_analyzer.domain.errors import ClassificationViolation
from return_platform.graph_schema_analyzer.domain.source_snapshot import SampleClassification

__all__ = ["SamplingPolicy"]

# A ceiling the analyzer imposes regardless of configuration. A misconfigured
# policy asking for a million rows is a data-exfiltration shape, not a schema
# analysis, so it is refused rather than clamped silently.
MAX_PERMITTED_SAMPLE_ROWS = 100


class SamplingPolicy(BaseModel):
    """One source's sampling rules, resolved from source configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    sampling_enabled: bool = False
    max_sample_rows: int = Field(default=0, ge=0, le=MAX_PERMITTED_SAMPLE_ROWS)
    retention_classification: SampleClassification = SampleClassification.NONE
    # Only meaningful for REDACTED: the field names permitted to survive the
    # redactor. Empty means "nothing survives", which is deliberately a usable
    # configuration -- it yields an empty sample rather than an error.
    retention_allowlist: frozenset[str] = frozenset()
    retention_period: timedelta = timedelta(days=7)

    @model_validator(mode="after")
    def _retention_is_coherent_with_access(self) -> SamplingPolicy:
        if self.retention_classification is not SampleClassification.NONE:
            if not self.sampling_enabled:
                raise ClassificationViolation(
                    f"source {self.source_id} disables sampling but asks to retain "
                    f"{self.retention_classification} samples; nothing would be read to retain."
                )
            if self.retention_period <= timedelta(0):
                raise ClassificationViolation(
                    f"source {self.source_id} retains {self.retention_classification} samples "
                    "but declares a non-positive retention period."
                )
        return self

    @property
    def effective_sample_limit(self) -> int:
        """Rows discovery may actually read. Zero when sampling is off, so the
        caller does not have to remember to check the flag separately."""
        return self.max_sample_rows if self.sampling_enabled else 0

    @property
    def retains_samples(self) -> bool:
        return self.retention_classification is not SampleClassification.NONE

    @classmethod
    def metadata_only(cls, source_id: str) -> SamplingPolicy:
        """The safe default for a source with no explicit sampling configuration:
        read no rows, retain nothing."""
        return cls(source_id=source_id)
