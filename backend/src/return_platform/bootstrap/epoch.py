"""Epoch-keyed two-phase reconfiguration mechanism (design doc section 13.2).

Replaces a single reconfigure() with a fenced, replica-atomic sequence: every module
prepares a candidate, and only if ALL of them are ready does ONE pointer swap make the
new epoch current. A request captures its epoch once and keeps it for its whole life;
old-epoch resources are released only after every holder has let go.

Epoch pointer, drain state, and lease admission all live in ONE object
(EpochAdmission) behind one lock. Keeping the pointer and the lease counts in two
independent objects is a TOCTOU race: a reader could observe the current epoch, and
before it registers as a holder, a concurrent reconfiguration could swap past it and
release that epoch's resources out from under it.

RuntimeEpoch is "replica-local" by design (see its docstring in platform/contracts/) --
the allocator and admission tracker here are in-process only, not distributed or
persisted. A future phase may back the allocator with the fenced, persisted tokens in
platform/system_store/ (design doc section 13.7) for cross-process coordination; that
is a distinct concern from the in-process visibility mechanism built here.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
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


class EpochLifecycleState(StrEnum):
    CURRENT = "CURRENT"
    DRAINING = "DRAINING"
    RELEASED = "RELEASED"


class EpochStateError(RuntimeError):
    """An operation was attempted against an epoch in an invalid state for it."""


@dataclass
class _EpochRecord:
    epoch: RuntimeEpoch
    state: EpochLifecycleState
    holders: int = 0


class EpochAdmission:
    """The single source of truth for which epoch is current, which are draining, and
    how many holders each has.

    Pointer swap, drain-state transition, and lease admission share one lock, so a
    request can never acquire a lease on an epoch whose resources have already been
    released -- the race a separate pointer-object/lease-tracker-object split allows.

    A plain threading.Lock (not asyncio.Lock) guards every method: every critical
    section here is synchronous with no `await` inside it, so the lock is held for a
    bounded, tiny duration and protects against both concurrent asyncio tasks and real
    OS threads (e.g. sync route handlers run in a thread pool).

    State machine per epoch: CURRENT -> DRAINING -> RELEASED. New leases are only ever
    handed out for CURRENT (acquire_current() cannot even express "give me a specific
    past epoch"); a lease acquired while CURRENT remains valid to release after the
    epoch moves to DRAINING; CURRENT can never be released; RELEASED is terminal and
    idempotent to request again.
    """

    def __init__(self, initial: RuntimeEpoch) -> None:
        self._lock = threading.Lock()
        self._records: dict[int, _EpochRecord] = {
            initial.epoch: _EpochRecord(epoch=initial, state=EpochLifecycleState.CURRENT)
        }
        self._current_epoch_number = initial.epoch

    @property
    def current(self) -> RuntimeEpoch:
        with self._lock:
            return self._records[self._current_epoch_number].epoch

    def acquire_current(self) -> RuntimeEpoch:
        """Atomically read the current epoch and register a holder on it."""
        with self._lock:
            record = self._records[self._current_epoch_number]
            record.holders += 1
            return record.epoch

    def release(self, epoch: RuntimeEpoch) -> None:
        """Release a previously acquired lease.

        Idempotent: releasing an epoch with zero holders, or one no longer tracked at
        all (already fully released), is a no-op rather than an error.
        """
        with self._lock:
            record = self._records.get(epoch.epoch)
            if record is None or record.holders <= 0:
                return
            record.holders -= 1

    def begin_swap(self, new_epoch: RuntimeEpoch) -> RuntimeEpoch:
        """Make `new_epoch` CURRENT and move the previous CURRENT epoch to DRAINING.

        Returns the epoch that is now draining.
        """
        with self._lock:
            previous_record = self._records[self._current_epoch_number]
            previous_record.state = EpochLifecycleState.DRAINING
            self._records[new_epoch.epoch] = _EpochRecord(
                epoch=new_epoch, state=EpochLifecycleState.CURRENT
            )
            self._current_epoch_number = new_epoch.epoch
            return previous_record.epoch

    def try_release(self, epoch: RuntimeEpoch) -> bool:
        """Transition a fully-drained DRAINING epoch to RELEASED.

        Raises EpochStateError if `epoch` is CURRENT -- the actively serving epoch can
        never be released. Returns False without raising if it is DRAINING but still
        held, or already RELEASED (idempotent). Returns True only on the transition
        that actually released it.
        """
        with self._lock:
            record = self._records.get(epoch.epoch)
            if record is None or record.state is EpochLifecycleState.RELEASED:
                return False
            if record.state is EpochLifecycleState.CURRENT:
                raise EpochStateError(f"epoch {epoch.epoch} is CURRENT and cannot be released")
            if record.holders > 0:
                return False
            record.state = EpochLifecycleState.RELEASED
            return True


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


class ReplicaStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class FatalReconfigurationError(RuntimeError):
    """A commit_reconfigure raised, or the replica is already UNAVAILABLE from a prior
    one. The replica cannot safely continue serving; it requires a restart.

    Design doc section 13.2: "If a commit nevertheless raises, the replica marks
    itself UNAVAILABLE and requires restart rather than serving a partial promotion."
    Rollback is not attempted -- a raised commit means some modules already made their
    candidate addressable and others did not, and there is no safe way to undo that.
    """

    def __init__(self, *, epoch: RuntimeEpoch, failed_module: str, message: str) -> None:
        super().__init__(message)
        self.epoch = epoch
        self.failed_module = failed_module


class ReconfigurationCoordinator:
    """Runs the epoch-keyed two-phase reconfiguration sequence over a fixed set of
    modules (design doc section 13.2).

    `modules` is a Mapping so a fatal commit failure can report which module_id
    failed, and because dict iteration order is insertion order, the caller controls
    prepare/commit/abort sequencing by construction order (typically the same
    dependency order used for initialization).
    """

    def __init__(
        self,
        modules: Mapping[str, Reconfigurable],
        admission: EpochAdmission,
    ) -> None:
        self._modules = modules
        self._admission = admission
        self._status = ReplicaStatus.AVAILABLE

    @property
    def status(self) -> ReplicaStatus:
        return self._status

    def acquire_current(self) -> RuntimeEpoch:
        """Admit a new request onto the current epoch.

        Raises FatalReconfigurationError if the replica is UNAVAILABLE -- once a
        commit has failed, this replica refuses new work rather than serve on
        possibly-inconsistent module state.
        """
        if self._status is ReplicaStatus.UNAVAILABLE:
            raise FatalReconfigurationError(
                epoch=self._admission.current,
                failed_module="<replica>",
                message="replica is UNAVAILABLE; refusing new request admission",
            )
        return self._admission.acquire_current()

    def release(self, epoch: RuntimeEpoch) -> None:
        self._admission.release(epoch)

    async def reconfigure(self, epoch: RuntimeEpoch) -> RuntimeEpoch | None:
        """Attempt to adopt `epoch`.

        Returns the retired (now-draining) epoch on success, or None if every
        module's live state is untouched because at least one returned
        RESTART_REQUIRED or raised during prepare. Either way, EVERY module -- not
        just the ones that already prepared -- receives abort_reconfigure for the
        refused epoch, because a module can allocate real resources before deciding
        to refuse, or before failing outright.

        Raises FatalReconfigurationError if the replica is already UNAVAILABLE, or if
        commit_reconfigure raises during this attempt (which also marks the replica
        UNAVAILABLE for all future calls).
        """
        if self._status is ReplicaStatus.UNAVAILABLE:
            raise FatalReconfigurationError(
                epoch=epoch,
                failed_module="<replica>",
                message="replica is UNAVAILABLE from a prior fatal commit failure",
            )

        prepared: list[Reconfigurable] = []
        refused = False
        prepare_exception: Exception | None = None
        for module in self._modules.values():
            try:
                outcome = await module.prepare_reconfigure(epoch)
            except Exception as exc:
                prepare_exception = exc
                refused = True
                break
            if outcome is ReconfigureOutcome.RESTART_REQUIRED:
                refused = True
                break
            prepared.append(module)

        if refused:
            await self._abort_all(epoch)
            if prepare_exception is not None:
                raise prepare_exception
            return None

        return await self._commit_and_swap(epoch)

    async def _abort_all(self, epoch: RuntimeEpoch) -> None:
        """Abort the candidate epoch on every module, not just the ones that already
        prepared successfully -- the refusing or failing module itself can have
        partial resources to clean up. Best-effort: every module gets an abort
        attempt regardless of earlier abort failures.
        """
        errors: list[Exception] = []
        for module in reversed(list(self._modules.values())):
            try:
                await module.abort_reconfigure(epoch)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("abort_reconfigure failed on one or more modules", errors)

    async def _commit_and_swap(self, epoch: RuntimeEpoch) -> RuntimeEpoch:
        for module_id, module in self._modules.items():
            try:
                await module.commit_reconfigure(epoch)
            except Exception as exc:
                self._status = ReplicaStatus.UNAVAILABLE
                raise FatalReconfigurationError(
                    epoch=epoch,
                    failed_module=module_id,
                    message=(
                        f"commit_reconfigure raised on module {module_id!r}; replica is "
                        f"now UNAVAILABLE and requires restart: {exc}"
                    ),
                ) from exc
        return self._admission.begin_swap(epoch)

    async def release_if_drained(self, epoch: RuntimeEpoch) -> bool:
        """Call release_epoch on every module for `epoch`, but only once it has fully
        drained. Returns whether release happened this call.

        Raises EpochStateError if `epoch` is still CURRENT -- see
        EpochAdmission.try_release.
        """
        released = self._admission.try_release(epoch)
        if not released:
            return False
        for module in self._modules.values():
            await module.release_epoch(epoch)
        return True
