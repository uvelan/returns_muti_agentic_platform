"""Put the schema file into the release store at startup, once, and never twice.

`resolve_active_schema` prefers a published, activated release and falls back to
the file. Every installation started with nothing published, so the file was
winning by *fallback* rather than by decision -- and that difference is not
visible from the console. An operator reading the releases list saw an empty
table and a platform that was demonstrably running some schema, with nothing
naming which one. Editing the file changed the runtime; editing it on an
installation that had published once changed nothing. Same file, same edit,
opposite outcomes, and no way to tell which installation you were on.

Seeding makes the store the answer in both cases. The file becomes what it
reads like -- the starting point an installation ships with -- and the question
"what schema is running" has one place to look.

**It will not overwrite an active release.** If a pointer exists, this does
nothing at all: the alternative is a platform that reverts an operator's
activation on every restart, which is a worse failure than the one being fixed
and an almost impossible one to diagnose from the outside.

**Release identity is bound to content.** The file names a fixed
`configuration_release_id`, so an edited file would otherwise re-publish the
same id with different content -- exactly what `publish` refuses, and rightly.
The seeded id carries the checksum, so an edited file is a different release
and an unedited one is the release already there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_store import (
    ReleaseAlreadyPublished,
    SchemaReleaseStore,
)

_LOGGER = logging.getLogger(__name__)

#: How much of the checksum goes into the release id. Long enough that two
#: schemas colliding is not a thing that happens, short enough that the id
#: stays something a person can read out loud.
_CHECKSUM_PREFIX = 12


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    """What seeding did, in a form the startup log and a test can both read."""

    #: SEEDED, ALREADY_ACTIVE, OPERATOR_RELEASE_ACTIVE, or UNAVAILABLE.
    status: str
    configuration_release_id: str | None = None
    detail: str | None = None


def seeded_release_id(base_id: str, checksum: str) -> str:
    """The id the file's current content publishes under."""
    return f"{base_id}-{checksum[:_CHECKSUM_PREFIX]}"


async def seed_release_from_file(
    path: Path, releases: SchemaReleaseStore, *, published_by: str = "bootstrap"
) -> SeedOutcome:
    """Publish and activate the file's schema, if nothing is active yet.

    Idempotent in the way that matters: run against a store this already seeded
    from an unchanged file, it finds its own release active and stops. Run
    against a store an operator has activated something in, it stops without
    touching it, whether or not the file has since changed.
    """
    schema = load_active_schema(path)
    release_id = seeded_release_id(
        schema.configuration_release_id, schema.configuration_checksum
    )

    active = await releases.active()
    if active is not None:
        if active.configuration_checksum == schema.configuration_checksum:
            return SeedOutcome("ALREADY_ACTIVE", active.configuration_release_id)
        # Deliberately not an error and deliberately not an overwrite. An
        # operator activating a release and a file that has since moved on are
        # both ordinary; which one should win is their decision, and the
        # console is where it gets made.
        return SeedOutcome(
            "OPERATOR_RELEASE_ACTIVE",
            active.configuration_release_id,
            detail=(
                "an activated release is already serving; the schema file was not "
                "seeded over it"
            ),
        )

    # `configuration_release_id` is the only field changed, and it is changed so
    # that the id names *this content*. Everything else is the file as written.
    seeded = schema.model_copy(update={"configuration_release_id": release_id})
    try:
        await releases.publish(seeded, published_by=published_by)
    except ReleaseAlreadyPublished:
        # Published on an earlier boot and then deactivated, or two workers
        # racing this on the same startup. Either way the release exists with
        # this exact content, and activating it is still the right end state.
        _LOGGER.info("schema_release_seed_already_published", extra={"release_id": release_id})
    await releases.activate(release_id)
    return SeedOutcome("SEEDED", release_id)
