"""`source_samples` — the only place analyzer sample rows are ever written.

Always encrypted at rest, regardless of classification: `REDACTED` means the
plaintext was passed through the redactor *before* sealing, `ENCRYPTED` means raw
content was sealed. Both are sealed. The structure is declared `encrypted: true`
in the manifest, so `SystemStore` itself refuses a plaintext write here -- the
envelope is not a convention this class chose to follow, it is the only shape the
store will accept.

Mirrors `platform/reasoning/evidence_store.py`'s seal/unseal pattern deliberately
rather than inventing a second one: same envelope fields, same metadata-allowlist
discipline, same insert-once semantics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["SOURCE_SAMPLES", "SourceSampleRepository"]

SOURCE_SAMPLES = "source_samples"

# Everything outside the envelope. Deliberately minimal: an id, a retention
# stamp, and nothing describing the content -- a field name leaking into
# plaintext metadata would defeat the point of sealing the rows.
_METADATA_FIELDS = frozenset({"samples_ref", "expires_at"})


class SourceSampleRepository:
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

    def _unseal(self, envelope: Mapping[str, Any]) -> bytes:
        return self._encryptor.decrypt(
            EnvelopePayload(
                ciphertext=envelope["ciphertext"],
                key_ref=envelope["key_ref"],
                algorithm=envelope["algorithm"],
                version=envelope["version"],
            )
        )

    async def save(
        self,
        *,
        samples_ref: str,
        rows_by_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        expires_at: datetime,
    ) -> None:
        """Seal and write one snapshot's samples under a single reference.

        `expires_at` is required, not optional: this collection carries a TTL
        index, and a document written without it would be retained forever --
        exactly what section 13.6 forbids for retained source content.
        """
        raw = json.dumps({k: list(v) for k, v in rows_by_dataset.items()}).encode("utf-8")
        await self._store.replace_one(
            SOURCE_SAMPLES,
            {"samples_ref": samples_ref},
            {
                "_id": samples_ref,
                "samples_ref": samples_ref,
                "expires_at": expires_at,
                "_envelope": self._seal(raw),
            },
            upsert=True,
            allowed_metadata_fields=_METADATA_FIELDS,
        )

    async def load(self, samples_ref: str) -> Mapping[str, list[dict[str, Any]]] | None:
        """Returns None for an expired or never-written reference.

        Expiry is normal, not exceptional: a snapshot's metadata outlives its
        samples by design, so callers must handle absence rather than treat it
        as corruption.
        """
        document = await self._store.read_only(SOURCE_SAMPLES).find_one(
            {"samples_ref": samples_ref}, {"_id": 0}
        )
        if document is None:
            return None
        decoded: Mapping[str, list[dict[str, Any]]] = json.loads(
            self._unseal(document["_envelope"]).decode("utf-8")
        )
        return decoded
