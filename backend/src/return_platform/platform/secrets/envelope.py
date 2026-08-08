"""Envelope encryption primitives (design doc §13.6).

`system_store/encryption.py` depends only on the presence and shape of the envelope
marker on a document, not on any concrete encryptor, so this module's implementation
can be swapped without touching the store layer that enforces the refusal.

Field names (`ciphertext`, `key_ref`, `algorithm`, `version`) match what
`EncryptionGuard.check_document` validates -- the guard checks these exact keys are
present under the envelope, not merely that an envelope key exists at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH_BYTES = 12
_ALGORITHM_AES_256_GCM = "AES-256-GCM"
_ENVELOPE_VERSION_V1 = "v1"


@dataclass(frozen=True, slots=True)
class EnvelopePayload:
    ciphertext: bytes
    key_ref: str
    algorithm: str
    version: str


class EnvelopeEncryptor(Protocol):
    key_ref: str

    def encrypt(self, plaintext: bytes) -> EnvelopePayload: ...

    def decrypt(self, payload: EnvelopePayload) -> bytes: ...


class EnvelopeDecryptionError(RuntimeError):
    """Raised when a payload cannot be decrypted (wrong key, tampered ciphertext)."""


class AesGcmEnvelopeEncryptor:
    """A real, working envelope encryptor: AES-256-GCM with a locally-held key.

    Interim implementation, not the final one -- a real KMS-backed encryptor (envelope
    keys per tenant/rotation, remote unwrap) is a separate, larger concern than a
    reasoning checkpointer needs to unblock. This is still genuine encryption (AEAD,
    unique nonce per message, authenticated) backed by a key resolved once at process
    startup (see `secrets/runtime.py`'s Vault-backed settings resolution) and held only
    in memory -- never persisted alongside the ciphertext it protects.
    """

    def __init__(self, *, key: bytes, key_ref: str) -> None:
        if len(key) != 32:
            raise ValueError("AesGcmEnvelopeEncryptor requires a 256-bit (32-byte) key")
        self.key_ref = key_ref
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> EnvelopePayload:
        nonce = os.urandom(_NONCE_LENGTH_BYTES)
        ciphertext = nonce + self._aead.encrypt(nonce, plaintext, None)
        return EnvelopePayload(
            ciphertext=ciphertext,
            key_ref=self.key_ref,
            algorithm=_ALGORITHM_AES_256_GCM,
            version=_ENVELOPE_VERSION_V1,
        )

    def decrypt(self, payload: EnvelopePayload) -> bytes:
        if payload.algorithm != _ALGORITHM_AES_256_GCM:
            raise EnvelopeDecryptionError(f"Unsupported algorithm: {payload.algorithm!r}")
        if payload.key_ref != self.key_ref:
            raise EnvelopeDecryptionError(
                f"Payload was sealed under key_ref {payload.key_ref!r}, "
                f"this encryptor holds {self.key_ref!r}"
            )
        nonce, ciphertext = (
            payload.ciphertext[:_NONCE_LENGTH_BYTES],
            payload.ciphertext[_NONCE_LENGTH_BYTES:],
        )
        try:
            return self._aead.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise EnvelopeDecryptionError("Envelope ciphertext failed authentication") from exc
