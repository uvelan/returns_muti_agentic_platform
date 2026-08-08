"""Checkpoint content allowlist enforcement.

Distinct from `platform.redaction.AllowlistRedactor`: that one silently drops
unlisted fields (appropriate for an audit payload, where dropping noise is the point).
A checkpoint violation must never be silently stripped -- LangGraph re-executes an
interrupted node from its beginning on resume, so a silently-dropped field would
resurface on the very next write, quietly changing what a checkpoint contains instead
of surfacing the mistake in development.

Never allowlist: Vault secrets, database passwords, API keys, credential-bearing
connection strings, raw configuration snapshots, raw unredacted source documents,
large customer records, provider authentication headers. Use references instead:
`configuration_release_id`, `graph_generation_id`, `source_snapshot_id`, `evidence_ref`,
`query_execution_id`, `candidate_id`, `schema_revision_id`.
"""

from __future__ import annotations

from collections.abc import Mapping

from return_platform.platform.reasoning.errors import CheckpointRedactionViolation


class CheckpointRedactor:
    def __init__(self, allowed_keys: frozenset[str]) -> None:
        self._allowed_keys = allowed_keys

    def enforce(self, state: Mapping[str, object]) -> Mapping[str, object]:
        violations = tuple(sorted(set(state) - self._allowed_keys))
        if violations:
            raise CheckpointRedactionViolation(
                f"Checkpoint state contains key(s) outside the declared allowlist: "
                f"{violations}. Store a reference (e.g. *_ref, *_id) instead of the "
                f"raw value, or add the key to the component's allowlist if it is "
                f"genuinely safe to persist."
            )
        return state
