"""Neutral configuration-pinning contract.

RuntimeConfigurationView is immutable and names exactly one release; it is the only
object with `section()`. RuntimeConfigurationHandle resolves views and cannot itself be
read, so a caller holding a view can never observe a release other than the one it was
given (design doc section 7.1, section 13.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeConfigurationView(Protocol):
    """An immutable window onto exactly one configuration release.

    `release_id`/`checksum` are read-only properties, not plain attributes: a plain
    `x: str` Protocol member means "readable AND writable" to mypy, which the natural
    frozen implementation cannot satisfy.
    """

    @property
    def release_id(self) -> str: ...
    @property
    def checksum(self) -> str: ...

    def section(self, key: str) -> Mapping[str, object]:
        """Raw configuration for one module, from this release only."""


from return_platform.platform.contracts.epoch import RuntimeEpoch

@runtime_checkable
class RuntimeConfigurationHandle(Protocol):
    """Resolves views. Deliberately has no read method of its own."""

    def current(self, epoch: RuntimeEpoch) -> RuntimeConfigurationView:
        """The release this replica has adopted for this epoch. Never for a pinned session."""

    async def pinned(self, release_id: str) -> RuntimeConfigurationView:
        """Raise ReleaseNotRetained if `release_id` has fallen out of retention."""

    @property
    def adopted_release_id(self) -> str: ...

    @property
    def pending_release_id(self) -> str | None: ...

    @property
    def requires_restart(self) -> bool: ...


class ReleaseNotRetained(RuntimeError):
    """A pinned() call named a release whose retention window has expired."""
