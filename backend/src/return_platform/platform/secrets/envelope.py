"""Envelope encryption primitives (design doc §13.6).

Deliberately minimal for now: the shape that a structure declared `encrypted: true` will
use once Phase 9 wires a real KMS-backed implementation. `system_store/encryption.py`
depends only on the presence and shape of the envelope marker on a document, not on any
concrete encryptor, so this module can gain a real implementation later without touching
the store layer that enforces the refusal.

Field names (`ciphertext`, `key_ref`, `algorithm`, `version`) match what
`EncryptionGuard.check_document` validates -- the guard checks these exact keys are
present under the envelope, not merely that an envelope key exists at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
