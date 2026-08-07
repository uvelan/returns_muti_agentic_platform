"""Epoch-keyed two-phase reconfiguration mechanism (design doc section 13.2).

Replaces a single reconfigure() with a fenced, replica-atomic sequence: every module
prepares a candidate, and only if ALL of them are ready does ONE pointer swap make the
new epoch current. A request captures its epoch once and keeps it for its whole life;
old-epoch resources are released only after every holder has let go.

RuntimeEpoch is "replica-local" by design (see its docstring in platform/contracts/) --
the allocator and pointer here are in-process only, not distributed or persisted. A
future phase may back the allocator with the fenced, persisted tokens in
platform/system_store/ (design doc section 13.7) for cross-process coordination; that
is a distinct concern from the in-process visibility mechanism built here.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome


@dataclass(frozen=True, slots=True)
class SimpleRuntimeEpoch:
    """Structurally satisfies RuntimeEpoch."""

    epoch: int
    release_id: str


class EpochAllocator:
    """Mints monotonically increasing, replica-local epoch numbers."""

    def __init__(self, start: int = 0) -> None:
        self._counter = itertools.count(start + 1)

    def next(self, release_id: str) -> SimpleRuntimeEpoch:
        return SimpleRuntimeEpoch(epoch=next(self._counter), release_id=release_id)


class EpochPointer:
    """The single replica-scoped 'which epoch is current' pointer.

    A plain attribute swap is the atomic operation: CPython reference reassignment is
    atomic under the GIL, so a reader calling `.current` never observes a torn value --
    it sees either the epoch before the swap or the one after, never a mix.
    """

    def __init__(self, initial: RuntimeEpoch) -> None:
        self._current = initial

    @property
    def current(self) -> RuntimeEpoch:
        return self._current

    def swap(self, new_epoch: RuntimeEpoch) -> RuntimeEpoch:
        """Make `new_epoch` current. Returns the epoch that was current before."""
        previous = self._current
        self._current = new_epoch
        return previous


class EpochLeaseTracker:
    """Counts in-flight holders per epoch so a drained epoch can be identified.

    `acquire()` increments; `release()` decrements. `release_epoch()` must only be
    called on a module once `is_drained()` is true for that epoch.
    """

    def __init__(self) -> None:
        self._counts: dict[int, int] = {}

    def acquire(self, epoch: RuntimeEpoch) -> None:
        self._counts[epoch.epoch] = self._counts.get(epoch.epoch, 0) + 1

    def release(self, epoch: RuntimeEpoch) -> None:
        remaining = self._counts.get(epoch.epoch, 0) - 1
        if remaining <= 0:
            self._counts.pop(epoch.epoch, None)
        else:
            self._counts[epoch.epoch] = remaining

    def is_drained(self, epoch: RuntimeEpoch) -> bool:
        return self._counts.get(epoch.epoch, 0) <= 0


@runtime_checkable
class Reconfigurable(Protocol):
    """The minimal shape ReconfigurationCoordinator needs.

    Every ModuleRuntime satisfies this structurally; test doubles only need to
    implement these four methods, not the full ModuleRuntime surface.
    """

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome: ...
    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None: ...
    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None: ...
    async def release_epoch(self, epoch: RuntimeEpoch) -> None: ...


class ReconfigurationCoordinator:
    """Runs the epoch-keyed two-phase reconfiguration sequence over a fixed set of
    modules (design doc section 13.2)."""

    def __init__(
        self,
        modules: Sequence[Reconfigurable],
        pointer: EpochPointer,
        leases: EpochLeaseTracker,
    ) -> None:
        self._modules = modules
        self._pointer = pointer
        self._leases = leases

    async def reconfigure(self, epoch: RuntimeEpoch) -> RuntimeEpoch | None:
        """Attempt to adopt `epoch`.

        Returns the retired epoch on success, or None if every module's live state is
        untouched because at least one returned RESTART_REQUIRED. If a module raises
        during prepare, every already-prepared module is aborted and the exception
        propagates -- prepare is documented as "safe to abandon," so this is the
        designed recovery path, not a special case.
        """
        prepared: list[Reconfigurable] = []
        for module in self._modules:
            try:
                outcome = await module.prepare_reconfigure(epoch)
            except Exception:
                for already_prepared in prepared:
                    await already_prepared.abort_reconfigure(epoch)
                raise
            if outcome is ReconfigureOutcome.RESTART_REQUIRED:
                for already_prepared in prepared:
                    await already_prepared.abort_reconfigure(epoch)
                return None
            prepared.append(module)

        for module in self._modules:
            await module.commit_reconfigure(epoch)

        return self._pointer.swap(epoch)

    async def release_if_drained(self, epoch: RuntimeEpoch) -> bool:
        """Call release_epoch on every module for `epoch`, but only if it has fully
        drained. Returns whether release happened. Never releases a still-held epoch.
        """
        if not self._leases.is_drained(epoch):
            return False
        for module in self._modules:
            await module.release_epoch(epoch)
        return True
