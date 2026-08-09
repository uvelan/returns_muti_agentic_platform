"""Audit emission for analyzer decisions.

Narrow by design: the analyzer records *that* a decision happened and what it was
attributable to (analysis, snapshot content hash, actor), never the payload it
reasoned over. Source samples and model prompts must not reach the audit log --
they are governed by section 13.6 and belong in classified storage, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["AnalyzerAuditPort"]


@runtime_checkable
class AnalyzerAuditPort(Protocol):
    async def record(
        self,
        *,
        analysis_id: str,
        action: str,
        actor: str,
        attributes: Mapping[str, str],
    ) -> None:
        """`attributes` is deliberately `Mapping[str, str]`, not `Mapping[str, Any]`:
        a flat string map cannot accidentally carry a nested sample row or a model
        response body the way an open value type invites."""
