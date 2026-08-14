"""The tool layer: every source inspection an analysis can perform, scope-checked.

This is the only object a reasoning loop, an API route, or an operator screen is
given. It holds an `InspectionScope` and a `SourceInspectionPort`, and there is no
path through it that reaches the port without the scope having been consulted
first -- which is the whole point. A model that names a table it was not granted
gets `ScopeViolation` from code that ran regardless of what the prompt said.

The check runs in both directions, and the second half is not redundant:

* **Inbound**, a request naming an ungranted source, object or field is refused.
* **Outbound**, listings are filtered and rows are projected down to granted
  fields before returning.

Inbound alone would leave `list_objects` naming every table in the warehouse and
`sample` returning every column of a row -- the model would never have to ask for
the ungranted name, because it was handed it. Outbound alone would let a refusal
look like an empty result. Both, and the grant holds whichever way a caller
approaches it.

Bounds are enforced the same way: `sample` and `profile` clamp to the source's
`max_sample_rows`, so no argument value can widen what a source yields.

Scope decides *which* values a caller may see; W4.6's masking decides *what a
value looks like* once it may be seen. `build_scoped_source_inspection` composes
both, and the ordering is set out in `sample_masking`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from return_platform.graph_schema_analyzer.application.sample_masking import (
    build_masked_source_inspection,
)
from return_platform.graph_schema_analyzer.composition import default_sample_masker
from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import InspectionScope
from return_platform.graph_schema_analyzer.ports.masking_port import SampleMaskingPort
from return_platform.graph_schema_analyzer.ports.source_port import (
    IndexDescription,
    ObjectDescription,
    ObjectProfile,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)

__all__ = ["ScopedSourceInspection", "build_scoped_source_inspection"]


class ScopedSourceInspection:
    """Structurally satisfies `SourceInspectionPort`, minus what scope removes.

    Deliberately the same shape as the port it wraps, so it can be substituted
    wherever the port is expected and no caller can hold the unwrapped one by
    accident. `list_sources` narrows to the grant rather than reporting what the
    connector could reach, because an analysis being told a source exists is
    already more than it was granted.
    """

    def __init__(self, inspection: SourceInspectionPort, *, scope: InspectionScope) -> None:
        self._inspection = inspection
        self._scope = scope

    @property
    def scope(self) -> InspectionScope:
        return self._scope

    async def validate(self, *, source_id: str) -> SourceValidation:
        self._scope.source_scope(source_id)
        return await self._inspection.validate(source_id=source_id)

    async def list_sources(self) -> Sequence[str]:
        """Intersected with what the connector can actually reach, so a grant for
        a source nobody configured does not appear as an available one."""
        reachable = set(await self._inspection.list_sources())
        return tuple(source_id for source_id in self._scope.source_ids() if source_id in reachable)

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        source_scope = self._scope.source_scope(source_id)
        objects = await self._inspection.list_objects(source_id=source_id)
        return tuple(item for item in objects if source_scope.permits_object(item.object_name))

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        permitted = self._permitted_fields(source_id, object_name)
        description = await self._inspection.describe_object(
            source_id=source_id, object_name=object_name
        )
        if permitted is None:
            return description
        return description.model_copy(
            update={
                "fields": tuple(
                    field for field in description.fields if field.field_name in permitted
                )
            }
        )

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Narrow the projection before the read, then again after it.

        The pre-read narrowing is what keeps an ungranted column from crossing the
        network at all. The post-read filter is what holds when a backend has no
        projection for the shape in question -- a Mongo document with an
        unanticipated key, say -- and would otherwise return more than was asked
        for.
        """
        permitted = self._permitted_fields(source_id, object_name)
        if permitted is not None and not permitted:
            # An empty grant means "no field here is readable", and it has to be
            # refused before the projection is built: an empty column list
            # reaches the backend as `SELECT  FROM ...`, and the operator would
            # be debugging a syntax error instead of reading a scope refusal.
            raise ScopeViolation(
                f"no field of {source_id!r}.{object_name!r} is in scope; "
                "the object is granted with an empty field list."
            )
        if fields is not None:
            requested = self._scope.require_fields(
                source_id=source_id, object_name=object_name, requested=fields
            )
        elif permitted is not None:
            requested = tuple(sorted(permitted))
        else:
            requested = None
        rows = await self._inspection.sample(
            source_id=source_id,
            object_name=object_name,
            limit=self._scope.sample_limit(source_id=source_id, requested=limit),
            fields=requested,
        )
        if permitted is None:
            return rows
        return tuple({key: value for key, value in row.items() if key in permitted} for row in rows)

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        permitted = self._permitted_fields(source_id, object_name)
        profile = await self._inspection.profile(
            source_id=source_id,
            object_name=object_name,
            sample_size=self._scope.sample_limit(source_id=source_id, requested=sample_size),
        )
        if permitted is None:
            return profile
        return profile.model_copy(
            update={
                "fields": tuple(field for field in profile.fields if field.field_name in permitted)
            }
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        """An index over an ungranted field is dropped whole rather than reported
        with that field removed: a composite index minus one column describes an
        access path that does not exist, and the analyzer would plan against it."""
        permitted = self._permitted_fields(source_id, object_name)
        indexes = await self._inspection.list_indexes(source_id=source_id, object_name=object_name)
        if permitted is None:
            return indexes
        return tuple(index for index in indexes if set(index.fields) <= permitted)

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        """Both endpoints must be in scope. A relationship whose far side is
        ungranted still discloses that the far object exists and what it is keyed
        by, which is the disclosure the object grant was drawn to prevent."""
        source_scope = self._scope.source_scope(source_id)
        if object_name is not None:
            source_scope.object_scope(object_name)
        observed = await self._inspection.list_relationships(
            source_id=source_id, object_name=object_name
        )
        return tuple(
            item
            for item in observed
            if source_scope.permits_object(item.from_object)
            and source_scope.permits_object(item.to_object)
        )

    def _permitted_fields(self, source_id: str, object_name: str) -> frozenset[str] | None:
        """Resolving the grant is also what refuses an ungranted source or object,
        because `permitted_fields` walks both levels to get there."""
        return self._scope.permitted_fields(source_id=source_id, object_name=object_name)


def build_scoped_source_inspection(
    inspection: SourceInspectionPort,
    *,
    scope: InspectionScope,
    masker: SampleMaskingPort | None = None,
) -> SourceInspectionPort:
    """Typed factory, and the reason it exists is not decoration.

    The return annotation is what makes mypy prove that the scoped wrapper still
    satisfies the port -- so a method added to `SourceInspectionPort` later
    cannot be answered by the unscoped adapter while this class quietly lacks it,
    which would be a hole that type-checks and reads as complete.

    Masking is applied outside the scope check, and a caller that supplies no
    `masker` gets a fresh one rather than an unmasked port. The default is what
    matters: an opt-in mask is a mask the first caller written in a hurry does
    not have, and there is no legitimate reason to hand a *reasoning loop*
    unmasked rows. A caller that needs tokens to line up across several analyses
    -- or a test that needs them to be reproducible -- passes its own.
    """
    return build_masked_source_inspection(
        ScopedSourceInspection(inspection, scope=scope),
        masker=default_sample_masker() if masker is None else masker,
    )
