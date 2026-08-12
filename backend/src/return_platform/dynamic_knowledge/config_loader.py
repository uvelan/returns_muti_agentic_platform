"""Load and checksum-verify an immutable active configuration release.

Two origins, one model. A release the Graph Schema Analyzer published and
activated is what the runtime loads once one exists; the YAML file is the
starting state of an installation that has never published, and the fallback
when the store cannot be reached. Both produce the same `ActiveSchema` -- there
is no second representation and no merge between them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import yaml

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.schema import ActiveSchema

logger = logging.getLogger("return_platform.dynamic_knowledge.config_loader")


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


class PublishedReleaseSource(Protocol):
    """The one thing resolution needs from the release store."""

    async def active(self) -> ActiveSchema | None: ...


async def resolve_active_schema(
    path: Path, releases: PublishedReleaseSource | None = None
) -> ActiveSchema:
    """The schema the runtime should run.

    A published, activated release wins over the file -- that is the whole
    point of the analyzer, and until this existed an approved schema changed
    nothing. Absent one, the file: an installation that has never published has
    to start somewhere, and refusing to boot would make the analyzer a
    prerequisite for the platform rather than a way to change it.

    A store that raises falls back to the file with a loud log rather than
    taking the runtime down. The file is a real schema that was running until
    the last publish, so degraded is behind, not broken -- and a Mongo blip
    should not stop an associate finding an order.
    """
    if releases is None:
        return load_active_schema(path)
    try:
        published = await releases.active()
    except Exception:  # noqa: BLE001 - a stale schema beats no platform
        logger.exception("published_schema_unavailable_using_file")
        return load_active_schema(path)
    if published is None:
        return load_active_schema(path)
    logger.info(
        "active_schema_from_published_release",
        extra={"configuration_release_id": published.configuration_release_id},
    )
    return published
