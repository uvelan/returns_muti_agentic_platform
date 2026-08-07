"""Platform-neutral clock contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Returns the current time. Domain code never calls datetime.now() directly."""

    def now(self) -> datetime: ...
