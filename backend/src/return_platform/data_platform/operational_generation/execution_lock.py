from uuid import UUID


class ExecutionLock:
    def __init__(self) -> None:
        self._locks: set[UUID] = set()

    def acquire(self, run_id: UUID) -> bool:
        if run_id in self._locks:
            return False
        self._locks.add(run_id)
        return True

    def release(self, run_id: UUID) -> None:
        self._locks.discard(run_id)
