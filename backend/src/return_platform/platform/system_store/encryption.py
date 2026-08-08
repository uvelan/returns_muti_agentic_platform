"""Refuses a plaintext write to a structure declared `encrypted: true` (design doc
§13.6, Slice 3R.6).

This validates the envelope's *shape*, not merely that an `_envelope` key is present:
`ciphertext`, `key_ref`, `algorithm`, and `version` must all be present under the
envelope (matching `platform.secrets.envelope.EnvelopePayload`'s fields). It also
rejects any top-level document field that isn't the envelope itself, `_id`, or an
explicitly declared metadata field -- `{"_envelope": {...}, "password": "plaintext"}`
does not pass just because the envelope itself is well-formed.

`SystemStore` is the only caller; business code that writes to an encrypted structure
must encrypt first and pass the resulting envelope document through, declaring which
(if any) additional top-level fields are legitimate routing/index metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PlaintextWriteRejected(RuntimeError):
    """Raised when a document without a well-formed envelope, or with unauthorized
    plaintext fields alongside the envelope, is written to a structure declared
    `encrypted: true`."""


class EncryptionGuard:
    ENVELOPE_KEY = "_envelope"
    REQUIRED_ENVELOPE_FIELDS = frozenset({"ciphertext", "key_ref", "algorithm", "version"})
    _ALWAYS_ALLOWED_TOP_LEVEL_FIELDS = frozenset({"_id", ENVELOPE_KEY})

    def check_document(
        self,
        logical_name: str,
        document: Mapping[str, Any],
        *,
        encrypted: bool,
        allowed_metadata_fields: frozenset[str] = frozenset(),
    ) -> None:
        if not encrypted:
            return

        envelope = document.get(self.ENVELOPE_KEY)
        if not isinstance(envelope, Mapping):
            raise PlaintextWriteRejected(
                f"Structure '{logical_name}' is declared encrypted=true; refusing to "
                f"write a document without an envelope-encrypted payload "
                f"(missing '{self.ENVELOPE_KEY}')"
            )

        missing = self.REQUIRED_ENVELOPE_FIELDS - envelope.keys()
        if missing:
            raise PlaintextWriteRejected(
                f"Structure '{logical_name}' envelope is missing required field(s): "
                f"{sorted(missing)}"
            )

        allowed = self._ALWAYS_ALLOWED_TOP_LEVEL_FIELDS | allowed_metadata_fields
        unexpected = set(document.keys()) - allowed
        if unexpected:
            raise PlaintextWriteRejected(
                f"Structure '{logical_name}' has unauthorized top-level field(s) "
                f"alongside its envelope: {sorted(unexpected)} -- only "
                f"{sorted(allowed)} are permitted"
            )
