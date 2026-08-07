"""Focused check: if a module's initialize() raises, every module already
initialized is shut down in reverse order before the failure propagates -- the caller
never holds a partially-initialized module list (design doc section 2.1 step 12).
"""

from __future__ import annotations

import pytest

from return_platform.platform.modules.lifecycle import initialize_all


class RecordingModule:
    def __init__(self, name: str, *, fail_init: bool = False, log: list[str] | None = None) -> None:
        self.name = name
        self._fail_init = fail_init
        self._log = log if log is not None else []

    async def initialize(self) -> None:
        if self._fail_init:
            raise RuntimeError(f"{self.name} failed to initialize")
        self._log.append(f"init:{self.name}")

    async def shutdown(self) -> None:
        self._log.append(f"shutdown:{self.name}")


@pytest.mark.asyncio
async def test_failed_initialization_shuts_down_prior_modules() -> None:
    log: list[str] = []
    module_a = RecordingModule("a", log=log)
    module_b = RecordingModule("b", log=log)
    module_c = RecordingModule("c", fail_init=True, log=log)
    module_d = RecordingModule("d", log=log)

    with pytest.raises(RuntimeError, match="c failed to initialize"):
        await initialize_all([module_a, module_b, module_c, module_d])

    # a and b were initialized before c failed; both get shut down, in reverse order.
    # c never finished initializing and is not shut down; d was never reached.
    assert log == ["init:a", "init:b", "shutdown:b", "shutdown:a"]


@pytest.mark.asyncio
async def test_cleanup_failure_is_reported_alongside_the_original_failure() -> None:
    class FailingShutdownModule(RecordingModule):
        async def shutdown(self) -> None:
            raise RuntimeError(f"{self.name} failed to shut down")

    log: list[str] = []
    module_a = FailingShutdownModule("a", log=log)
    module_b = RecordingModule("b", fail_init=True, log=log)

    with pytest.raises(ExceptionGroup) as excinfo:
        await initialize_all([module_a, module_b])

    messages = {str(exc) for exc in excinfo.value.exceptions}
    assert "b failed to initialize" in messages
    assert "a failed to shut down" in messages
