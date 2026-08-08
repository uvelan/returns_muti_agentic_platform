"""Durable, encrypted storage for full QueryEvidence -- the "evidence by
reference" half of "no raw source records in checkpoint state" (Phase 7 /
Wave C2).

A LangGraph checkpoint holds only `query_execution_id` values
(`evidence_refs`); the full `QueryEvidence` (including its raw `result`) is
written once here, at creation time, by whichever node produced it, and
rehydrated by `query_execution_id` only where it's actually needed (model
invocation, response evidence validation) -- never re-persisted into graph
state. Naturally insert-once, like a checkpoint: a query execution's result
is immutable once observed, so no state machine is needed here the way
`receipts.py`'s side-effect tracking needs one.

Retention is stamped alongside checkpoints/writes/receipts by
`CheckpointRetentionPolicy.mark_terminal`, keyed by `run_id` -- evidence must
never outlive (or be reaped before) the run whose reasoning it backs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence
from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

_STRUCTURE = "order_discovery_query_evidence"
_METADATA_FIELDS = frozenset(
    {"query_execution_id", "run_id", "schema_version", "graph_generation_id", "created_at"}
)


class QueryEvidenceStore:
    """This is durable evidence storage, never the authoritative business
    record -- canonical state stays in the graph and Mongo's own business
    collections. No business reasoning lives here."""

    def __init__(self, system_store: SystemStore, encryptor: EnvelopeEncryptor) -> None:
        self._store = system_store
        self._encryptor = encryptor

    def _seal(self, raw: bytes) -> dict[str, Any]:
        payload = self._encryptor.encrypt(raw)
        return {
            "ciphertext": payload.ciphertext,
            "key_ref": payload.key_ref,
            "algorithm": payload.algorithm,
            "version": payload.version,
        }

    def _unseal(self, envelope: dict[str, Any]) -> bytes:
        payload = EnvelopePayload(
            ciphertext=envelope["ciphertext"],
            key_ref=envelope["key_ref"],
            algorithm=envelope["algorithm"],
            version=envelope["version"],
        )
        return self._encryptor.decrypt(payload)

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        """Insert one QueryEvidence, keyed by its own query_execution_id.

        Idempotent: a retried write for the same query_execution_id (e.g. a
        LangGraph node re-executed after a crash, per the module docstring's
        "naturally insert-once" reasoning) is a silent no-op rather than a
        duplicate-key error, as long as the run_id agrees -- the query result
        itself is immutable once observed, so there is nothing to reconcile.
        """
        raw = json.dumps(evidence.model_dump(mode="json")).encode("utf-8")
        document = {
            "_id": evidence.query_execution_id,
            "query_execution_id": evidence.query_execution_id,
            "run_id": run_id,
            "schema_version": evidence.schema_version,
            "graph_generation_id": evidence.graph_generation_id,
            "created_at": datetime.now(UTC),
            "_envelope": self._seal(raw),
        }
        try:
            await self._store.insert_one(
                _STRUCTURE, document, allowed_metadata_fields=_METADATA_FIELDS
            )
        except Exception:
            existing = await self._store.read_only(_STRUCTURE).find_one(
                {"_id": evidence.query_execution_id}
            )
            if existing is None:
                raise
            if existing.get("run_id") != run_id:
                raise ValueError(
                    f"query_execution_id {evidence.query_execution_id!r} already recorded "
                    f"under a different run_id"
                ) from None

    async def get(self, query_execution_id: str) -> QueryEvidence | None:
        doc = await self._store.read_only(_STRUCTURE).find_one({"_id": query_execution_id})
        if doc is None:
            return None
        raw = self._unseal(doc["_envelope"])
        return QueryEvidence.model_validate(json.loads(raw))

    async def get_many(self, query_execution_ids: Sequence[str]) -> tuple[QueryEvidence, ...]:
        """Rehydrate several evidence_refs in the order requested. Raises if
        any referenced id is missing -- a checkpoint must never carry a
        dangling evidence_ref."""
        evidence: list[QueryEvidence] = []
        for query_execution_id in query_execution_ids:
            found = await self.get(query_execution_id)
            if found is None:
                raise KeyError(f"no query evidence recorded for {query_execution_id!r}")
            evidence.append(found)
        return tuple(evidence)
