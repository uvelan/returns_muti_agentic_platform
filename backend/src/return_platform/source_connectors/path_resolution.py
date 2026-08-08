"""Shared logical-field-id -> physical-path resolution.

Extracted from `MongoDBSourceScanConnector._resolve_physical_field` /
`SqlServerSourceScanConnector._resolve_physical_column`, which were ~90%
duplicate (Phase 8 / Wave C1): both resolve a logical `field_id` (never a
physical document path or column name) through every `EntityDefinition`
backed by one source, requiring they all agree on one physical path.
"""

from __future__ import annotations

from return_platform.dynamic_knowledge.schema import ActiveSchema


def resolve_physical_path(
    schema: ActiveSchema,
    *,
    source_asset_id: str,
    field_id: str,
    error_cls: type[Exception],
    noun: str = "physical paths",
) -> tuple[str, ...]:
    """Resolve `field_id` to its physical_path, requiring every entity backed
    by `source_asset_id` to agree. Returns the raw path segments; callers
    decide how to address their target with them (dot-joined for MongoDB,
    a single flat segment for SQL Server -- see each connector's own
    flatness/joining logic). `noun` lets each connector keep its own existing
    error wording ("physical paths" vs "physical columns")."""

    physical_paths: set[tuple[str, ...]] = set()
    for entity in schema.entities.values():
        if entity.source_asset_id != source_asset_id:
            continue
        field = entity.fields.get(field_id)
        if field is None or not field.physical_path:
            continue
        physical_paths.add(tuple(field.physical_path))
    if not physical_paths:
        raise error_cls(
            f"source {source_asset_id!r}'s field {field_id!r} has no physical_path "
            "on any entity backed by this source"
        )
    if len(physical_paths) > 1:
        raise error_cls(
            f"source {source_asset_id!r}'s field {field_id!r} maps to different "
            f"{noun} across entities: {sorted(physical_paths)}"
        )
    return next(iter(physical_paths))
