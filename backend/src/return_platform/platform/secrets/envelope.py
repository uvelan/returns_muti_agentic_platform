"""Envelope encryption primitives (design doc §13.6).

Deliberately minimal for now: the shape that a structure declared `encrypted: true` will
use once Phase 9 wires a real KMS-backed implementation. `system_store/encryption.py`
depends only on the presence of the envelope marker on a document, not on any concrete
encryptor, so this module can gain a real implementation later without touching the
store layer that enforces the refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EnvelopePayload:
    ciphertext: bytes
    key_id: str
    nonce: bytes


class EnvelopeEncryptor(Protocol):
    key_id: str

    def encrypt(self, plaintext: bytes) -> EnvelopePayload: ...

    def decrypt(self, payload: EnvelopePayload) -> bytes: ...
