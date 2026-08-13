"""Structured-payload field redaction. See README.md."""

from return_platform.platform.contracts.redaction import Redactor
from return_platform.platform.redaction.allowlist import AllowlistRedactor
from return_platform.platform.redaction.sample_masking import MASK_PREFIX, SampleMasker
from return_platform.platform.redaction.sensitive_keys import (
    SENSITIVE_KEY_FRAGMENTS,
    is_sensitive_key,
    normalize_key,
)

__all__ = [
    "MASK_PREFIX",
    "SENSITIVE_KEY_FRAGMENTS",
    "AllowlistRedactor",
    "Redactor",
    "SampleMasker",
    "is_sensitive_key",
    "normalize_key",
]
