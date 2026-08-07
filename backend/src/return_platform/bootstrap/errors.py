"""Startup failure classification (design doc section 2.1, referenced from lifespan.py).

Fail-closed in production, degrade in development -- but which environment a replica
is running in is a canonical-configuration concept (bootstrap/settings.py, not yet
built). This module defines the failure taxonomy and the decision function;
lifespan.py wires it to a real environment value once that exists.
"""

from __future__ import annotations

from enum import StrEnum


class StartupFailureSeverity(StrEnum):
    FATAL = "FATAL"  # always stops startup, in every environment
    DEGRADABLE = "DEGRADABLE"  # stops startup in production; logs and continues in dev


class StartupFailure(RuntimeError):
    """A classified startup failure, carrying the phase that failed and its severity."""

    def __init__(self, phase: str, severity: StartupFailureSeverity, cause: Exception) -> None:
        super().__init__(f"startup failed in phase {phase!r} ({severity.value}): {cause}")
        self.phase = phase
        self.severity = severity
        self.cause = cause


def should_stop_startup(severity: StartupFailureSeverity, *, is_production: bool) -> bool:
    """FATAL always stops startup. DEGRADABLE stops only in production."""
    return severity is StartupFailureSeverity.FATAL or is_production
