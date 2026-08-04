"""Generic physical path resolution without business-field knowledge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class PathResolutionError(ValueError):
    """Raised when a configured physical path cannot be resolved safely."""


def resolve_path(record: object, path: Sequence[str]) -> object:
    """Resolve mapping keys and list indexes from a configured path."""

    current: object = record
    for segment in path:
        if isinstance(current, Mapping):
            if segment not in current:
                raise PathResolutionError(f"missing configured path segment {segment!r}")
            current = current[segment]
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            if not segment.isdecimal():
                raise PathResolutionError(
                    f"list path segment must be a non-negative integer: {segment!r}"
                )
            index = int(segment)
            if index >= len(current):
                raise PathResolutionError(f"list path index out of range: {index}")
            current = current[index]
            continue
        raise PathResolutionError(
            f"cannot traverse segment {segment!r} through {type(current).__name__}"
        )
    return current


def resolve_optional(record: object, path: Sequence[str]) -> object | None:
    """Resolve a path and return ``None`` when it is absent."""

    try:
        return resolve_path(record, path)
    except PathResolutionError:
        return None
