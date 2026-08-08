"""Refuses a plaintext write to a structure declared `encrypted: true` (design doc §13.6).

This is a shape check, not a cryptographic one: it only confirms a document has already
been wrapped by an `EnvelopeEncryptor` (carries the envelope marker key) before it is
allowed anywhere near a structure declared encrypted. `SystemStore` is the only caller;
business code that writes to an encrypted structure must encrypt first and pass the
resulting envelope document through.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PlaintextWriteRejected(RuntimeError):
    """Raised when a document without an envelope-encrypted payload is written to a
    structure declared `encrypted: true`."""


class EncryptionGuard:
    ENVELOPE_KEY = "_envelope"

    def check_document(
        self, logical_name: str, document: Mapping[str, Any], *, encrypted: bool
    ) -> None:
        if encrypted and self.ENVELOPE_KEY not in document:
            raise PlaintextWriteRejected(
                f"Structure '{logical_name}' is declared encrypted=true; refusing to "
                f"write a document without an envelope-encrypted payload "
                f"(missing '{self.ENVELOPE_KEY}')"
            )
