"""Shared contracts and validators for observed database metadata."""

from collections.abc import Hashable, Iterable
from datetime import datetime, timedelta
from itertools import pairwise

from pydantic import BaseModel, ConfigDict


class ObservedMetadataModel(BaseModel):
    """Base model for immutable physically observed metadata."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


def require_unique[IdentifierT: Hashable](
    values: Iterable[IdentifierT],
    *,
    label: str,
) -> None:
    """Reject duplicate physical identifiers."""

    seen: set[IdentifierT] = set()

    for value in values:
        if value in seen:
            raise ValueError(
                f"Duplicate {label} detected.",
            )

        seen.add(value)


def require_strictly_ascending_integers(
    values: tuple[int, ...],
    *,
    label: str,
) -> None:
    """Require deterministic ascending integer ordering."""

    if any(current >= following for current, following in pairwise(values)):
        raise ValueError(
            f"{label} must be strictly ascending.",
        )


def require_strictly_ascending_text(
    values: tuple[str, ...],
    *,
    label: str,
) -> None:
    """Require deterministic lexicographical ordering."""

    if any(current >= following for current, following in pairwise(values)):
        raise ValueError(
            f"{label} must be strictly ascending.",
        )


def require_utc_timestamp(
    value: datetime,
) -> datetime:
    """Require a timezone-aware timestamp using UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "Observation timestamp must be timezone-aware.",
        )

    if value.utcoffset() != timedelta(0):
        raise ValueError(
            "Observation timestamp must use UTC.",
        )

    return value
