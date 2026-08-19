"""Seal and open the one secret a configured source carries.

WHY

`save_source` wrote the connection password into the system store as plaintext.
Anyone with read access to that collection -- a backup, a log of a slow query, an
operator with the wrong grant -- had every configured source's credential. The
API never returned it, which is necessary and was not sufficient: the exposure
was at rest, not on the wire.

WHAT

`platform/secrets/envelope.py` already implements AES-256-GCM envelope
encryption with a key held only in memory, resolved once at startup. This is the
binding: one sealed field in, one plaintext string out, and nothing else in the
analyzer needs to know either.

MIGRATION

A document written before this exists still holds `password` as a string.
`open_credential` accepts both shapes, so an existing deployment keeps working
and re-seals on the next save rather than needing a migration step that could
lock an operator out of their own sources.
"""

from __future__ import annotations

import base64
from typing import Any

from return_platform.platform.secrets.envelope import (
    AesGcmEnvelopeEncryptor,
    EnvelopeDecryptionError,
    EnvelopePayload,
)

#: The field a sealed credential is stored under. Distinct from `password` so a
#: reader can tell the two shapes apart without guessing at the value.
SEALED_FIELD = "passwordEnvelope"

KEY_REF = "graph-analyzer-source-credential"


class CredentialSealError(RuntimeError):
    """Raised when a stored credential cannot be opened."""


def _encryptor(key: bytes) -> AesGcmEnvelopeEncryptor:
    return AesGcmEnvelopeEncryptor(key=key, key_ref=KEY_REF)


def seal_credential(password: str, *, key: bytes) -> dict[str, Any]:
    """Return the stored representation of one credential."""
    payload = _encryptor(key).encrypt(password.encode("utf-8"))
    return {
        # Base64 because BSON binary round-trips less predictably through the
        # model dumps this document travels in.
        "ciphertext": base64.b64encode(payload.ciphertext).decode("ascii"),
        "key_ref": payload.key_ref,
        "algorithm": payload.algorithm,
        "version": payload.version,
    }


def open_credential(document: dict[str, Any], *, key: bytes) -> str:
    """Return the plaintext credential for one stored source.

    Accepts a document written before sealing existed, so an existing
    deployment is not locked out of its own sources by this change.
    """
    sealed = document.get(SEALED_FIELD)
    if isinstance(sealed, dict):
        try:
            payload = EnvelopePayload(
                ciphertext=base64.b64decode(str(sealed["ciphertext"])),
                key_ref=str(sealed["key_ref"]),
                algorithm=str(sealed["algorithm"]),
                version=str(sealed["version"]),
            )
            return _encryptor(key).decrypt(payload).decode("utf-8")
        except (KeyError, ValueError, EnvelopeDecryptionError) as error:
            raise CredentialSealError(
                "The stored credential for this source could not be opened. "
                "Re-enter the password to reconnect it."
            ) from error
    legacy = document.get("password")
    return legacy if isinstance(legacy, str) else ""


def has_credential(document: dict[str, Any]) -> bool:
    """Whether a source has a usable credential, in either shape."""
    return isinstance(document.get(SEALED_FIELD), dict) or bool(document.get("password"))
