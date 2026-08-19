"""A configured source's password is sealed at rest, not stored as text.

The API never returned the password, which was necessary and not sufficient:
the exposure was in the store. Anyone who could read that collection -- a
backup, a slow-query log, an operator with the wrong grant -- held every
configured source's credential.
"""

from __future__ import annotations

import base64
import json

import pytest

from return_platform.graph_analyzer.credentials import (
    SEALED_FIELD,
    CredentialSealError,
    has_credential,
    open_credential,
    seal_credential,
)

KEY = base64.b64decode("cHJvZHVjdGlvbi1yZWFzb25pbmcta2V5LTMyYnl0ZXM=")
OTHER_KEY = bytes(32)


def test_a_sealed_credential_does_not_contain_the_password() -> None:
    """The point of the whole module, asserted against the stored bytes."""
    sealed = seal_credential("hunter2-correct-horse", key=KEY)

    serialized = json.dumps(sealed)
    assert "hunter2-correct-horse" not in serialized
    assert sealed["algorithm"] == "AES-256-GCM"
    assert sealed["key_ref"] == "graph-analyzer-source-credential"


def test_a_sealed_credential_round_trips() -> None:
    sealed = seal_credential("s3cret!@%pass", key=KEY)

    assert open_credential({SEALED_FIELD: sealed}, key=KEY) == "s3cret!@%pass"


def test_the_same_password_seals_differently_each_time() -> None:
    """A fresh nonce per message, so equal passwords are not equal ciphertexts.

    Without it, two sources sharing a password would be visibly identical in the
    store, which leaks that they are the same without revealing what it is.
    """
    first = seal_credential("same", key=KEY)
    second = seal_credential("same", key=KEY)

    assert first["ciphertext"] != second["ciphertext"]


def test_opening_with_the_wrong_key_is_refused_rather_than_returning_rubbish() -> None:
    sealed = seal_credential("secret", key=KEY)

    with pytest.raises(CredentialSealError):
        open_credential({SEALED_FIELD: sealed}, key=OTHER_KEY)


def test_tampered_ciphertext_is_refused() -> None:
    """AES-GCM authenticates, so an edited ciphertext fails rather than decodes."""
    sealed = seal_credential("secret", key=KEY)
    raw = bytearray(base64.b64decode(sealed["ciphertext"]))
    raw[-1] ^= 0xFF
    tampered = {**sealed, "ciphertext": base64.b64encode(bytes(raw)).decode("ascii")}

    with pytest.raises(CredentialSealError):
        open_credential({SEALED_FIELD: tampered}, key=KEY)


def test_a_document_written_before_sealing_still_opens() -> None:
    """An existing deployment must not be locked out of its own sources.

    Those documents re-seal on the next save rather than needing a migration
    step that could strand an operator midway.
    """
    assert open_credential({"password": "legacy-plaintext"}, key=KEY) == "legacy-plaintext"


def test_a_source_with_no_credential_reports_none() -> None:
    assert has_credential({}) is False
    assert open_credential({}, key=KEY) == ""


@pytest.mark.parametrize(
    "document",
    [
        {SEALED_FIELD: {"ciphertext": "abc"}},
        {
            SEALED_FIELD: {
                "ciphertext": "!!not-base64!!",
                "key_ref": "x",
                "algorithm": "y",
                "version": "v1",
            }
        },
    ],
)
def test_a_malformed_envelope_is_refused_with_an_actionable_message(
    document: dict[str, object],
) -> None:
    with pytest.raises(CredentialSealError) as raised:
        open_credential(document, key=KEY)

    assert "Re-enter the password" in str(raised.value)


def test_has_credential_recognises_both_shapes() -> None:
    assert has_credential({SEALED_FIELD: seal_credential("x", key=KEY)}) is True
    assert has_credential({"password": "legacy"}) is True
