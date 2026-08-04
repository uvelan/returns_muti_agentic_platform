"""Immutable candidate sets and graph-generation-pinned conversation facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from return_platform.dynamic_knowledge.fingerprint import sha256_digest


class CandidateSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str
    conversation_id: str
    turn_id: str
    principal_id: str
    tenant_id: str
    schema_version: str
    graph_generation_id: str
    query_execution_id: str
    candidate_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    checksum: str

    @classmethod
    def create(
        cls,
        *,
        candidate_set_id: str,
        conversation_id: str,
        turn_id: str,
        principal_id: str,
        tenant_id: str,
        schema_version: str,
        graph_generation_id: str,
        query_execution_id: str,
        candidate_ids: tuple[str, ...],
        created_at: datetime,
        expires_at: datetime,
    ) -> CandidateSet:
        payload = {
            "candidate_set_id": candidate_set_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "principal_id": principal_id,
            "tenant_id": tenant_id,
            "schema_version": schema_version,
            "graph_generation_id": graph_generation_id,
            "query_execution_id": query_execution_id,
            "candidate_ids": candidate_ids,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        return cls(**payload, checksum=sha256_digest(payload))

    def validate_selection(
        self,
        *,
        candidate_id: str,
        conversation_id: str,
        principal_id: str,
        tenant_id: str,
        graph_generation_id: str,
        now: datetime,
    ) -> None:
        if conversation_id != self.conversation_id or principal_id != self.principal_id or tenant_id != self.tenant_id:
            raise ValueError("candidate set ownership mismatch")
        if graph_generation_id != self.graph_generation_id:
            raise ValueError("candidate set belongs to a stale graph generation")
        if now >= self.expires_at:
            raise ValueError("candidate set expired")
        if candidate_id not in self.candidate_ids:
            raise ValueError("candidate is not present in the immutable candidate set")
