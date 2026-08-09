"""Read-only discovery over configured sources.

Note what this port cannot express: there is no write, no DDL, and no free-form
query. Source systems are read-only to the analyzer as a structural fact of the
contract, not as a rule someone has to remember (design doc C3.3). Sampling is
bounded by the caller-supplied limit *and* by the source's own sampling policy on
the implementing side, so a large `limit` cannot escalate access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["DiscoveredDataset", "SourceDiscoveryPort"]


class DiscoveredDataset(BaseModel):
    """What discovery found for one dataset, before the analyzer classifies it.

    `sample_rows` is transient by contract: it may be handed to the AI call and
    must pass the platform redactor (or an encrypted structure) before any
    durable write. `SourceSchemaSnapshot` is what records which of those happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    dataset_name: str
    fields: tuple[Mapping[str, Any], ...]
    approximate_row_count: int | None = None
    sample_rows: tuple[Mapping[str, Any], ...] = ()


@runtime_checkable
class SourceDiscoveryPort(Protocol):
    """Resolved from the capability registry; implemented in bootstrap/adapters/."""

    async def list_source_ids(self) -> Sequence[str]:
        """Every source the caller is permitted to analyze."""

    async def discover(self, *, source_id: str, sample_limit: int) -> Sequence[DiscoveredDataset]:
        """Metadata for every dataset in one source, plus at most `sample_limit`
        rows per dataset when the source's policy permits sampling at all."""
