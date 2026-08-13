"""The port boundary where source values stop being source values.

W4.5 put scope here and deliberately left masking alone, so that responsibility
would land in one place rather than half in the tool layer and half in four
adapters. This is that one place.

**Why a decorator over the port rather than a step in the caller.** There is no
caller to put it in. `sample` is reached from a reasoning loop, from an operator
screen and from an API route, and a masking step written into any of them is a
masking step the other two do not have. Wrapping the port means the object a
caller is handed cannot return an unmasked row, whatever the caller does with it
-- the same argument `ScopedSourceInspection` makes for scope, and the reason
these two compose instead of merging.

**Masking is outermost.** `build_scoped_source_inspection` now returns
`Masked(Scoped(port))`, so scope runs first on real values and masking is the
last thing that touches a row on its way out. The other order would also work
today -- masking preserves keys, so the scope projection would still filter
correctly -- but it puts an unmasked value in the outer object's hands, and the
guarantee worth having is that the object callers hold has nothing unmasked left
in it.

**`profile` is not masked, and that is not an oversight.** It returns counts,
rates and candidacy flags: `null_rate=0.02`, `approximate_distinct=48`,
`identifier_candidate=True`. There is no value in it to mask -- the rows it was
computed from never leave `source_inspection_profiling` -- and masking it would
corrupt the statistics W4.8 ranks on while protecting nothing. `describe_object`,
`list_indexes` and `list_relationships` carry field and object *names* for the
same reason: they are structure, and structure is what the analysis is for.

**`SourceDiscoveryPort` is deliberately not wrapped.** It carries `sample_rows`
too, so wrapping it looks like the obvious symmetry -- and it would be wrong.
Those rows have a different destination: `DiscoveryService` hands them to
retention, where `SamplingPolicy` already decides between dropping them
(`NONE`), passing them through `AllowlistRedactor` (`REDACTED`) and sealing them
(`ENCRYPTED`). Masking at that port would quietly turn an `ENCRYPTED` retention
-- whose whole purpose is to hold the real values behind an access path -- into
a store of surrogates, and nothing would report that the classification the
snapshot claims is no longer what it holds.

The route that *does* need covering is the other one C3.5 named: a sample
rendered into a prompt. That is `build_prompt_blocks(untrusted_samples=...)` in
`prompt_context`, which masks with this same implementation before block 5 is
assembled. Two boundaries, one masker, and neither is a place a caller can route
around -- which is the property that matters, rather than there being literally
one call site.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from return_platform.graph_schema_analyzer.ports.source_port import (
    IndexDescription,
    ObjectDescription,
    ObjectProfile,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)
from return_platform.platform.redaction.sample_masking import SampleMasker

__all__ = ["MaskedSourceInspection", "build_masked_source_inspection"]


class MaskedSourceInspection:
    """Structurally satisfies `SourceInspectionPort`; every returned row is masked.

    Every other method delegates unchanged. Written out rather than produced with
    `__getattr__` so that a method added to the port later fails type-checking
    here instead of silently forwarding to the unmasked implementation -- which
    is exactly how a new value-bearing method would ship unmasked.
    """

    def __init__(self, inspection: SourceInspectionPort, *, masker: SampleMasker) -> None:
        self._inspection = inspection
        self._masker = masker

    async def validate(self, *, source_id: str) -> SourceValidation:
        return await self._inspection.validate(source_id=source_id)

    async def list_sources(self) -> Sequence[str]:
        return await self._inspection.list_sources()

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        return await self._inspection.list_objects(source_id=source_id)

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        return await self._inspection.describe_object(source_id=source_id, object_name=object_name)

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """The only method that carries values, and so the only one that masks."""
        rows = await self._inspection.sample(
            source_id=source_id, object_name=object_name, limit=limit, fields=fields
        )
        return self._masker.mask_rows(rows)

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        """Statistics, never a value -- see the module docstring."""
        return await self._inspection.profile(
            source_id=source_id, object_name=object_name, sample_size=sample_size
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        return await self._inspection.list_indexes(source_id=source_id, object_name=object_name)

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        return await self._inspection.list_relationships(
            source_id=source_id, object_name=object_name
        )


def build_masked_source_inspection(
    inspection: SourceInspectionPort, *, masker: SampleMasker
) -> SourceInspectionPort:
    """Typed factory. The annotation is what makes mypy prove the wrapper still
    satisfies the port, so a method added to `SourceInspectionPort` cannot be
    answered by the unmasked implementation while this class quietly lacks it."""
    return MaskedSourceInspection(inspection, masker=masker)
