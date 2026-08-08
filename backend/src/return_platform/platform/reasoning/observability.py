"""Reasoning-run trace emission.

Thin, typed convenience layer over `platform.audit.AuditSink` -- this package doesn't
invent a second audit mechanism, it just names the events a reasoning run's lifecycle
produces so every caller emits the same event_type/payload shape instead of
freehand-formatting one per call site.
"""

from __future__ import annotations

from datetime import UTC, datetime

from return_platform.platform.contracts.audit import AuditEvent, AuditSink


class ReasoningObservability:
    def __init__(self, audit: AuditSink) -> None:
        self._audit = audit

    async def run_started(
        self, *, run_id: str, thread_id: str, correlation_id: str, principal_id: str
    ) -> None:
        await self._audit.record(
            AuditEvent(
                event_type="reasoning.run_started",
                occurred_at=datetime.now(UTC),
                correlation_id=correlation_id,
                principal_id=principal_id,
                payload={"run_id": run_id, "thread_id": thread_id},
            )
        )

    async def run_terminal(
        self,
        *,
        run_id: str,
        thread_id: str,
        lifecycle_state: str,
        correlation_id: str,
        principal_id: str,
    ) -> None:
        await self._audit.record(
            AuditEvent(
                event_type="reasoning.run_terminal",
                occurred_at=datetime.now(UTC),
                correlation_id=correlation_id,
                principal_id=principal_id,
                payload={
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "lifecycle_state": lifecycle_state,
                },
            )
        )

    async def run_abandoned(
        self,
        *,
        run_id: str,
        thread_id: str,
        forced: bool,
        correlation_id: str,
        principal_id: str,
    ) -> None:
        """Abandonment is a business event: audited and visible, never a silent
        deletion (design doc, Phase 5A)."""
        await self._audit.record(
            AuditEvent(
                event_type="reasoning.run_abandoned",
                occurred_at=datetime.now(UTC),
                correlation_id=correlation_id,
                principal_id=principal_id,
                payload={"run_id": run_id, "thread_id": thread_id, "forced": forced},
            )
        )

    async def abandonment_blocked(
        self,
        *,
        run_id: str,
        blocking_reference: str,
        correlation_id: str,
        principal_id: str,
    ) -> None:
        await self._audit.record(
            AuditEvent(
                event_type="reasoning.abandonment_blocked",
                occurred_at=datetime.now(UTC),
                correlation_id=correlation_id,
                principal_id=principal_id,
                payload={"run_id": run_id, "blocking_reference": blocking_reference},
            )
        )
