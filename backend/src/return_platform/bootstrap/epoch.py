"""Epoch-keyed two-phase reconfiguration mechanism (design doc section 13.2).

Replaces a single reconfigure() with a fenced, replica-atomic sequence: every module
prepares a candidate, and only if ALL of them are ready does ONE pointer swap make the
new epoch current. A request captures its epoch once and keeps it for its whole life;
old-epoch resources are released only after every holder has let go.

Epoch pointer, drain state, holder counts, and admission-open/closed state all live in
ONE object (EpochAdmission) behind one lock. Splitting any of these across independent
objects reopens a TOCTOU race: a reader could observe a value, and before it acts on
that observation, a concurrent operation could invalidate it out from under the reader.
Two such races were found and closed here:

  - reading the current epoch and registering as a holder of it were two calls on two
    objects (fixed by EpochAdmission itself, in an earlier pass);
  - checking "is the replica UNAVAILABLE" and registering as a holder were still two
    calls on two synchronization domains (ReconfigurationCoordinator._status vs
    EpochAdmission's lock) -- fixed by moving admission-open/closed state into
    EpochAdmission too, so acquire_current() checks it under the same lock it uses to
    register the holder.

RuntimeEpoch is "replica-local" by design (see its docstring in platform/contracts/) --
the allocator and admission tracker here are in-process only, not distributed or
persisted. A future phase may back the allocator with the fenced, persisted tokens in
platform/system_store/ (design doc section 13.7) for cross-process coordination; that
is a distinct concern from the in-process visibility mechanism built here.
"""

from __future__ import annotations

import asyncio
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
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"


class EpochStateError(RuntimeError):
    """An operation was attempted against an epoch in an invalid state for it."""


class StaleReconfiguration(RuntimeError):
    """A reconfiguration attempted to swap based on an epoch that is no longer, or
    was never, current. The current epoch is never regressed; the caller should
    abandon this attempt rather than retry blindly."""


class ReplicaUnavailable(RuntimeError):
    """Admission is closed. The replica is not accepting new work and requires a
    restart -- see EpochAdmission.close()."""


@dataclass
class _EpochRecord:
    epoch: RuntimeEpoch
    state: EpochLifecycleState
    holders: int = 0


class EpochAdmission:
    """The single source of truth for which epoch is current, which are draining or
    releasing, how many holders each has, and whether the replica is still accepting
    new work.

    Pointer swap, drain-state transition, lease admission, and the accepting/closed
    flag all share one lock, so no caller can observe or act on a value that a
    concurrent operation has already superseded.

    A plain threading.Lock (not asyncio.Lock) guards every method: every critical
    section here is synchronous with no `await` inside it, so the lock is held for a
    bounded, tiny duration and protects against both concurrent asyncio tasks and real
    OS threads (e.g. sync route handlers run in a thread pool).

    State machine per epoch: CURRENT -> DRAINING -> RELEASING -> RELEASED. New leases
    are only ever handed out for CURRENT (acquire_current() cannot even express "give
    me a specific past epoch"); a lease acquired while CURRENT remains valid to
    release after the epoch moves to DRAINING; CURRENT can never be released;
    RELEASING exists so a module cleanup failure never finalizes RELEASED on
    unverified success -- see release_if_drained() in ReconfigurationCoordinator, which
    can retry from RELEASING; RELEASED is terminal and idempotent to request again.
    """

    def __init__(self, initial: RuntimeEpoch) -> None:
        self._lock = threading.Lock()
        self._records: dict[int, _EpochRecord] = {
            initial.epoch: _EpochRecord(epoch=initial, state=EpochLifecycleState.CURRENT)
        }
        self._current_epoch_number = initial.epoch
        self._accepting_requests = True

    @property
    def current(self) -> RuntimeEpoch:
        with self._lock:
            return self._records[self._current_epoch_number].epoch

    def is_accepting(self) -> bool:
        with self._lock:
            return self._accepting_requests

    def close(self) -> None:
        """Permanently stop admitting new requests. Idempotent. There is no reopen --
        a closed replica requires a restart."""
        with self._lock:
            self._accepting_requests = False

    def acquire_current(self) -> RuntimeEpoch:
        """Atomically check admission is open, read the current epoch, and register a
        holder on it.

        Raises ReplicaUnavailable if admission is closed -- checked under the same
        lock as the holder registration, so there is no window between "the replica
        just closed" and "a request was admitted anyway."
        """
        with self._lock:
            if not self._accepting_requests:
                raise ReplicaUnavailable("admission is closed; the replica is UNAVAILABLE")
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

    def begin_swap(
        self, new_epoch: RuntimeEpoch, expected_current_epoch: RuntimeEpoch
    ) -> RuntimeEpoch:
        """Make `new_epoch` CURRENT and move the previous CURRENT epoch to DRAINING.

        Raises StaleReconfiguration if `expected_current_epoch` is not actually
        CURRENT (a concurrent swap already moved past it) or if `new_epoch` is not
        strictly newer than the current epoch number. Both checks happen inside the
        same lock as the swap itself -- there is no window between validating and
        acting in which another swap could interleave. This is defense in depth
        behind ReconfigurationCoordinator's own serialization (see `reconfigure()`):
        it protects the invariant even if a future caller reaches this method
        directly, or two coordinator instances somehow shared one EpochAdmission.

        Returns the epoch that is now draining.
        """
        with self._lock:
            if self._current_epoch_number != expected_current_epoch.epoch:
                raise StaleReconfiguration(
                    f"expected current epoch {expected_current_epoch.epoch} but current "
                    f"is {self._current_epoch_number}; refusing to swap"
                )
            if new_epoch.epoch <= self._current_epoch_number:
                raise StaleReconfiguration(
                    f"new epoch {new_epoch.epoch} is not newer than current "
                    f"{self._current_epoch_number}; the current epoch is never regressed"
                )
            previous_record = self._records[self._current_epoch_number]
            previous_record.state = EpochLifecycleState.DRAINING
            self._records[new_epoch.epoch] = _EpochRecord(
                epoch=new_epoch, state=EpochLifecycleState.CURRENT
            )
            self._current_epoch_number = new_epoch.epoch
            return previous_record.epoch

    def begin_release(self, epoch: RuntimeEpoch) -> bool:
        """Transition a fully-drained DRAINING epoch to RELEASING, or confirm it is
        already RELEASING so a retried release attempt can proceed.

        Raises EpochStateError if `epoch` is CURRENT -- the actively serving epoch can
        never be released. Returns False if it is DRAINING but still held, or already
        RELEASED. Returns True if it is now, or already was, RELEASING.
        """
        with self._lock:
            record = self._records.get(epoch.epoch)
            if record is None or record.state is EpochLifecycleState.RELEASED:
                return False
            if record.state is EpochLifecycleState.CURRENT:
                raise EpochStateError(f"epoch {epoch.epoch} is CURRENT and cannot be released")
            if record.state is EpochLifecycleState.RELEASING:
                return True
            if record.holders > 0:
                return False
            record.state = EpochLifecycleState.RELEASING
            return True

    def finish_release(self, epoch: RuntimeEpoch) -> None:
        """Transition a RELEASING epoch to RELEASED, once every module's
        release_epoch has actually succeeded. Idempotent past RELEASED."""
        with self._lock:
            record = self._records.get(epoch.epoch)
            if record is None or record.state is EpochLifecycleState.RELEASED:
                return
            record.state = EpochLifecycleState.RELEASED


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

    Every reconfigure() call is serialized by an internal asyncio.Lock: prepare/commit
    span multiple `await` points, so without serialization two concurrent attempts for
    different target epochs could interleave their commits and have the OLDER one's
    swap land last, regressing the current epoch. EpochAdmission.begin_swap() also
    independently refuses a stale swap as defense in depth (see its docstring), but
    this lock is what prevents two attempts from ever running their prepare/commit
    phases concurrently in the first place.
    """

    def __init__(
        self,
        modules: Mapping[str, Reconfigurable],
        admission: EpochAdmission,
    ) -> None:
        self._modules = modules
        self._admission = admission
        self._reconfigure_lock = asyncio.Lock()

    @property
    def status(self) -> ReplicaStatus:
        """Derived from EpochAdmission's own accepting-requests flag -- the single
        source of truth. There is deliberately no separate coordinator-level status
        field to keep in sync with it."""
        return (
            ReplicaStatus.UNAVAILABLE
            if not self._admission.is_accepting()
            else ReplicaStatus.AVAILABLE
        )

    def acquire_current(self) -> RuntimeEpoch:
        """Admit a new request onto the current epoch.

        Raises FatalReconfigurationError if the replica is UNAVAILABLE -- once a
        commit has failed, this replica refuses new work rather than serve on
        possibly-inconsistent module state. The check and the admission happen
        atomically inside EpochAdmission; there is no window between them.
        """
        try:
            return self._admission.acquire_current()
        except ReplicaUnavailable as exc:
            raise FatalReconfigurationError(
                epoch=self._admission.current,
                failed_module="<replica>",
                message="replica is UNAVAILABLE; refusing new request admission",
            ) from exc

    def release(self, epoch: RuntimeEpoch) -> None:
        self._admission.release(epoch)

    async def reconfigure(self, epoch: RuntimeEpoch) -> RuntimeEpoch | None:
        """Attempt to adopt `epoch`.

        Serialized: only one reconfigure() call runs at a time on this coordinator, so
        two concurrent attempts can never interleave their prepare/commit phases.

        Returns the retired (now-draining) epoch on success, or None if every
        module's live state is untouched because at least one returned
        RESTART_REQUIRED or raised during prepare. Either way, EVERY module -- not
        just the ones that already prepared -- receives abort_reconfigure for the
        refused epoch, because a module can allocate real resources before deciding
        to refuse, or before failing outright.

        Raises FatalReconfigurationError if the replica is already UNAVAILABLE, or if
        commit_reconfigure raises during this attempt (which also marks the replica
        UNAVAILABLE for all future calls). Raises StaleReconfiguration if the current
        epoch moved between this call starting and its commit completing.
        """
        async with self._reconfigure_lock:
            if not self._admission.is_accepting():
                raise FatalReconfigurationError(
                    epoch=epoch,
                    failed_module="<replica>",
                    message="replica is UNAVAILABLE from a prior fatal commit failure",
                )

            expected_current_epoch = self._admission.current

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

            return await self._commit_and_swap(epoch, expected_current_epoch)

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

    async def _commit_and_swap(
        self, epoch: RuntimeEpoch, expected_current_epoch: RuntimeEpoch
    ) -> RuntimeEpoch:
        for module_id, module in self._modules.items():
            try:
                await module.commit_reconfigure(epoch)
            except Exception as exc:
                self._admission.close()
                raise FatalReconfigurationError(
                    epoch=epoch,
                    failed_module=module_id,
                    message=(
                        f"commit_reconfigure raised on module {module_id!r}; replica is "
                        f"now UNAVAILABLE and requires restart: {exc}"
                    ),
                ) from exc
        return self._admission.begin_swap(epoch, expected_current_epoch)

    async def release_if_drained(self, epoch: RuntimeEpoch) -> bool:
        """Call release_epoch on every module for `epoch`, once it has fully drained.

        Uses an intermediate RELEASING state (EpochAdmission.begin_release /
        finish_release) so a module cleanup failure never finalizes RELEASED on
        unverified success: the epoch stays retryable in RELEASING until every
        module's release_epoch has actually completed without raising. Modules are
        called again on retry even if they already succeeded once -- release_epoch is
        documented as must-not-fail / idempotent for exactly this reason, the same way
        abort_reconfigure must tolerate repeated or partial calls.

        Returns whether this call fully completed the release. Raises EpochStateError
        if `epoch` is still CURRENT (see EpochAdmission.begin_release). Raises
        ExceptionGroup, leaving the epoch in RELEASING for a future retry, if any
        module's release_epoch raises.
        """
        if not self._admission.begin_release(epoch):
            return False

        errors: list[Exception] = []
        for module in self._modules.values():
            try:
                await module.release_epoch(epoch)
            except Exception as exc:
                errors.append(exc)

        if errors:
            raise ExceptionGroup(
                "release_epoch failed on one or more modules; the epoch remains "
                "RELEASING and this call may be retried",
                errors,
            )

        self._admission.finish_release(epoch)
        return True
