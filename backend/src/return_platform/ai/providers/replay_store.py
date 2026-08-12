"""Where recorded provider answers live: the system store, like everything else.

**Not a second storage path.** The first version of this reached past the
`SystemStore` straight into a Mongo collection, which was wrong twice over: it
hard-coded the physical backend into application code, so moving the platform's
storage would have left replay behind; and it bypassed the guard that decides
what may be written in the clear. Every other durable platform structure --
interceptions, source samples, reasoning checkpoints -- is declared in
`config/platform/system_store.yaml` and reached through `SystemStore`, which
owns the logical-to-physical binding. Replay is not special enough to be the
exception.

**Sealed at rest, for the same reason interceptions are.** A recording is a
model's answer to a real prompt; for order discovery that answer names customers
and orders, and for the schema analyzer the prompt it answers carries rows read
out of a customer's database. The structure is declared `encrypted: true`, so
the store layer refuses a plaintext write and the only thing left outside the
envelope is the digest a lookup needs.

Keyed by request digest, so a recording is found by what was asked rather than
by when. Writes are upserts: re-recording the same question is the ordinary
consequence of a strict run being widened, not a conflict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["AI_REPLAY_RECORDINGS", "SystemStoreReplayStore"]

AI_REPLAY_RECORDINGS = "ai_replay_recordings"

#: What may sit outside the envelope. A digest is a lookup key and reveals
#: nothing about the question; the timestamps are operational. The recorded
#: answer itself never appears here.
_METADATA_FIELDS = frozenset({"digest", "recordedAt", "firstSeenAt"})


class SystemStoreReplayStore:
    """`ReplayStore` over the platform's declared storage.

    Satisfies the protocol structurally, so `ReplayProvider` neither knows nor
    cares which backend is configured -- which is the point of putting it here
    rather than binding a database client into the provider.
    """

    def __init__(self, store: SystemStore, encryptor: EnvelopeEncryptor) -> None:
        self._store = store
        self._encryptor = encryptor

    async def read(self, digest: str) -> dict[str, Any] | None:
        document = await self._store.read_only(AI_REPLAY_RECORDINGS).find_one({"digest": digest})
        if document is None:
            return None
        envelope = document.get("payload")
        if not isinstance(envelope, dict):
            return None
        decoded = json.loads(self._unseal(envelope).decode("utf-8"))
        return decoded if isinstance(decoded, dict) else None

    async def write(self, digest: str, record: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        sealed = self._seal(json.dumps(record, sort_keys=True).encode("utf-8"))
        existing = await self._store.read_only(AI_REPLAY_RECORDINGS).find_one({"digest": digest})
        # Kept from the first sighting rather than reset on every re-record: how
        # long a question has been in the corpus is the useful fact, and a field
        # that resets on write records nothing at all.
        first_seen = existing.get("firstSeenAt") if isinstance(existing, dict) else None
        await self._store.replace_one(
            AI_REPLAY_RECORDINGS,
            {"digest": digest},
            {
                "digest": digest,
                "payload": sealed,
                "recordedAt": now,
                "firstSeenAt": first_seen or now,
            },
            upsert=True,
            allowed_metadata_fields=_METADATA_FIELDS,
        )

    def _seal(self, raw: bytes) -> dict[str, Any]:
        """The same envelope shape `SystemStoreInterceptionStore` writes.

        Spelled out field by field rather than dumped from the dataclass so the
        stored document cannot drift if `EnvelopePayload` gains a field the
        guard has not been taught about.
        """
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
