"""What one analysis is permitted to inspect: sources, objects, fields, row bounds.

The reason this is a domain object and not a paragraph in a system prompt: a model
that names a table it was never granted has to be refused by code that runs whether
or not the prompt was followed. Grants live here so the refusal is a value
comparison, and `application/source_inspection.py` is the single place that has to
perform it -- rather than each of the four backend connectors getting its own
chance to forget.

`None` and the empty collection are deliberately different, and the difference is
the safe direction in both cases. `None` means "not narrowed at this level" --
every object of the source, every field of the object. An empty collection means
"nothing here is granted", so every request against it is refused. A scope built
from a partially-populated configuration therefore fails closed rather than
widening to everything, which is the failure mode that matters: over-refusing is
an operator complaint, over-granting is a data leak nobody reports.

Retention is not this object's business -- `SamplingPolicy` owns what may survive
the AI call. This owns what may be *read*, which is why both exist.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.sampling_policy import MAX_PERMITTED_SAMPLE_ROWS

__all__ = ["InspectionScope", "ObjectScope", "SourceScope"]


class ObjectScope(BaseModel):
    """One granted object, and optionally a narrowed set of its fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The object's fully-qualified name in whatever vocabulary its backend uses:
    # a Mongo collection, a `schema.table` for SQL, a node label for Neo4j. Kept
    # as one opaque string rather than parsed parts so that scope comparison is
    # exact-match on the same token the caller asked for -- a scope that has to
    # normalise before comparing is a scope with a bypass in it.
    object_name: str = Field(min_length=1)
    fields: frozenset[str] | None = None


class SourceScope(BaseModel):
    """One granted source, its objects, and how many rows it may yield."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    objects: tuple[ObjectScope, ...] | None = None
    # Zero is the default because a grant to inspect structure is not a grant to
    # read values. Bounded by the analyzer's own ceiling for the same reason
    # `SamplingPolicy` is: a configuration asking for a million rows describes an
    # export, not an analysis.
    max_sample_rows: int = Field(default=0, ge=0, le=MAX_PERMITTED_SAMPLE_ROWS)

    def object_scope(self, object_name: str) -> ObjectScope:
        if self.objects is None:
            return ObjectScope(object_name=object_name)
        for scope in self.objects:
            if scope.object_name == object_name:
                return scope
        raise ScopeViolation(
            f"object {object_name!r} is not in scope for source {self.source_id!r}; "
            f"granted: {sorted(scope.object_name for scope in self.objects)}"
        )

    def permits_object(self, object_name: str) -> bool:
        """Non-raising form, for filtering a connector's listing.

        `list_objects` must not fail because the source happens to contain an
        ungranted table -- it must simply not mention it. Asking for that table by
        name still raises; the two behaviours are different on purpose.
        """
        if self.objects is None:
            return True
        return any(scope.object_name == object_name for scope in self.objects)


class InspectionScope(BaseModel):
    """The whole grant for one analysis, across every source it may reach."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[SourceScope, ...] = ()

    def source_scope(self, source_id: str) -> SourceScope:
        for scope in self.sources:
            if scope.source_id == source_id:
                return scope
        raise ScopeViolation(
            f"source {source_id!r} is not in scope for this analysis; "
            f"granted: {sorted(scope.source_id for scope in self.sources)}"
        )

    def source_ids(self) -> tuple[str, ...]:
        return tuple(scope.source_id for scope in self.sources)

    def permitted_fields(self, *, source_id: str, object_name: str) -> frozenset[str] | None:
        """The field allowlist for one object, or `None` when unnarrowed."""
        return self.source_scope(source_id).object_scope(object_name).fields

    def require_fields(
        self, *, source_id: str, object_name: str, requested: Iterable[str]
    ) -> tuple[str, ...]:
        """Refuse a field selection naming anything outside the grant.

        Reports the whole offending set rather than the first one found, so an
        operator correcting a scope sees the full gap in one pass instead of
        rediscovering it a field at a time.
        """
        requested_fields = tuple(requested)
        permitted = self.permitted_fields(source_id=source_id, object_name=object_name)
        if permitted is None:
            return requested_fields
        refused = sorted(set(requested_fields) - permitted)
        if refused:
            raise ScopeViolation(
                f"fields {refused} are not in scope for {source_id!r}.{object_name!r}; "
                f"granted: {sorted(permitted)}"
            )
        return requested_fields

    def sample_limit(self, *, source_id: str, requested: int) -> int:
        """Clamp rather than refuse, and refuse only a request for nothing.

        A caller asking for more rows than the grant allows is asking a reasonable
        question badly, and answering with the granted number is more useful than
        an error. A source granted zero rows is a different statement -- it says
        values must not be read at all -- so that one raises.
        """
        scope = self.source_scope(source_id)
        if scope.max_sample_rows == 0:
            raise ScopeViolation(
                f"source {source_id!r} grants no sample rows; "
                "structure may be inspected but values may not be read."
            )
        if requested < 1:
            raise ScopeViolation(f"sample limit must be at least 1, got {requested!r}")
        return min(requested, scope.max_sample_rows)

    @classmethod
    def over_sources(
        cls, source_ids: Sequence[str], *, max_sample_rows: int = 0
    ) -> InspectionScope:
        """Whole-source grants, for the discovery path that has always read every
        object of a configured source. Named rather than inlined so that the
        broadest possible scope is something a reader can grep for."""
        return cls(
            sources=tuple(
                SourceScope(source_id=source_id, max_sample_rows=max_sample_rows)
                for source_id in source_ids
            )
        )
