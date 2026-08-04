"""Load and checksum-verify an immutable active configuration release."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.schema import ActiveSchema


class ConfigurationIntegrityError(ValueError):
    """Configuration release checksum or activation metadata is invalid."""


def load_active_schema(path: Path) -> ActiveSchema:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigurationIntegrityError("active schema must be a YAML object")
    supplied_checksum = raw.get("configuration_checksum")
    if not isinstance(supplied_checksum, str):
        raise ConfigurationIntegrityError("configuration_checksum is required")
    payload: dict[str, Any] = dict(raw)
    payload.pop("configuration_checksum", None)
    expected_checksum = sha256_digest(payload)
    if supplied_checksum != expected_checksum:
        raise ConfigurationIntegrityError(
            "active configuration checksum does not match its content"
        )
    return ActiveSchema.model_validate(raw)
