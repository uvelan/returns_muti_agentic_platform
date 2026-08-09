"""Immutable revision history and diffs.

Every mutation produces a revision recording *what changed and who asked for
it*. That matters more here than in most audit trails: a schema drives a graph
rebuild, so "why does the graph have this shape" has to be answerable months
later, and the answer is the ordered list of commands plus their author.

Revisions are append-only. There is no update method, and `sequence` is unique
per draft, so history cannot be quietly rewritten to make a past decision look
different from what was made.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.domain.mutation import MutationCommand

__all__ = ["ChangeType", "SchemaDiff", "SchemaDiffEntry", "SchemaRevision"]


class ChangeType(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"


class SchemaRevision(BaseModel):
    """One applied batch of mutations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: str
    draft_id: str
    sequence: int
    mutations: tuple[MutationCommand, ...]
    author: str
    created_at: datetime
    # Whether a human or the reasoning graph authored this batch. Recorded
    # because "the model changed this" and "an analyst changed this" carry very
    # different weight when reviewing why a schema looks the way it does.
    authored_by_model: bool = False

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")


class SchemaDiffEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    change_type: ChangeType
    element: str
    detail: str


class SchemaDiff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_sequence: int
    to_sequence: int
    entries: tuple[SchemaDiffEntry, ...]

    @property
    def is_empty(self) -> bool:
        return not self.entries


def diff_shapes(
    before: Mapping[str, Any], after: Mapping[str, Any], *, from_sequence: int, to_sequence: int
) -> SchemaDiff:
    """Diff two schema shapes at entity/relationship granularity.

    Entity-level rather than property-level on purpose: a reviewer asking "what
    changed between revision 3 and 7" wants the shape of the answer to match the
    shape of the decision, and a property-by-property listing of a renamed
    entity buries the one fact that matters.
    """
    entries: list[SchemaDiffEntry] = []
    before_entities: Mapping[str, Any] = before.get("entities", {}) or {}
    after_entities: Mapping[str, Any] = after.get("entities", {}) or {}

    for label in sorted(set(after_entities) - set(before_entities)):
        entries.append(
            SchemaDiffEntry(change_type=ChangeType.ADDED, element=label, detail="entity added")
        )
    for label in sorted(set(before_entities) - set(after_entities)):
        entries.append(
            SchemaDiffEntry(change_type=ChangeType.REMOVED, element=label, detail="entity removed")
        )
    for label in sorted(set(before_entities) & set(after_entities)):
        if before_entities[label] != after_entities[label]:
            entries.append(
                SchemaDiffEntry(
                    change_type=ChangeType.MODIFIED,
                    element=label,
                    detail=_describe_entity_change(before_entities[label], after_entities[label]),
                )
            )

    entries.extend(
        _relationship_entries(
            before.get("relationships", ()) or (), after.get("relationships", ()) or ()
        )
    )
    return SchemaDiff(from_sequence=from_sequence, to_sequence=to_sequence, entries=tuple(entries))


def _describe_entity_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    before_props = set(before.get("properties") or {})
    after_props = set(after.get("properties") or {})
    added = sorted(after_props - before_props)
    removed = sorted(before_props - after_props)
    parts: list[str] = []
    if added:
        parts.append(f"properties added: {', '.join(added)}")
    if removed:
        parts.append(f"properties removed: {', '.join(removed)}")
    if not parts:
        parts.append("definition changed")
    return "; ".join(parts)


def _relationship_entries(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[SchemaDiffEntry]:
    def key(item: Mapping[str, Any]) -> str:
        return f"{item.get('from_label')}-[{item.get('relationship_type')}]->{item.get('to_label')}"

    before_by_key = {key(item): item for item in before}
    after_by_key = {key(item): item for item in after}
    entries: list[SchemaDiffEntry] = []
    for name in sorted(set(after_by_key) - set(before_by_key)):
        entries.append(
            SchemaDiffEntry(change_type=ChangeType.ADDED, element=name, detail="relationship added")
        )
    for name in sorted(set(before_by_key) - set(after_by_key)):
        entries.append(
            SchemaDiffEntry(
                change_type=ChangeType.REMOVED, element=name, detail="relationship removed"
            )
        )
    for name in sorted(set(before_by_key) & set(after_by_key)):
        if before_by_key[name] != after_by_key[name]:
            entries.append(
                SchemaDiffEntry(
                    change_type=ChangeType.MODIFIED,
                    element=name,
                    detail=(
                        f"cardinality {before_by_key[name].get('cardinality')} -> "
                        f"{after_by_key[name].get('cardinality')}"
                    ),
                )
            )
    return entries
