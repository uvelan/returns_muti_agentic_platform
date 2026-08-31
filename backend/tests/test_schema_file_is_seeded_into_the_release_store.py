"""The file must become a release, and must never overwrite an operator's.

`resolve_active_schema` prefers a published, activated release and falls back
to the file. Every installation started with nothing published, so the file was
winning by fallback rather than by decision -- invisible from the console, and
the same edit to the same file changed the runtime on one installation and
nothing on another.

The dangerous half of fixing that is the overwrite: a seeder that published the
file over whatever was active would revert an operator's activation on every
restart. That is the property most of these tests are about.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_seed import (
    seed_release_from_file,
    seeded_release_id,
)
from return_platform.dynamic_knowledge.release_store import ReleaseAlreadyPublished

SCHEMA_PATH = Path("config/dynamic_knowledge/active-schema.return-order.yaml")


class FakeReleases:
    """The three operations seeding uses, and a record of what it did."""

    def __init__(self) -> None:
        self.published: dict[str, object] = {}
        self.pointer: str | None = None
        self.activations: list[str] = []

    async def active(self) -> object | None:
        return None if self.pointer is None else self.published[self.pointer]

    async def publish(self, schema: object, *, published_by: str) -> None:
        release_id = schema.configuration_release_id  # type: ignore[attr-defined]
        if release_id in self.published:
            raise ReleaseAlreadyPublished(release_id)
        self.published[release_id] = schema

    async def activate(self, configuration_release_id: str) -> None:
        self.activations.append(configuration_release_id)
        self.pointer = configuration_release_id


@pytest.fixture
def schema_path() -> Path:
    if not SCHEMA_PATH.exists():
        pytest.skip("the packaged schema file is not present in this checkout")
    return SCHEMA_PATH


@pytest.mark.asyncio
async def test_an_empty_store_gets_the_file(schema_path: Path) -> None:
    store = FakeReleases()
    outcome = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    assert outcome.status == "SEEDED"
    assert store.pointer == outcome.configuration_release_id


@pytest.mark.asyncio
async def test_the_release_id_names_the_content_not_just_the_file(schema_path: Path) -> None:
    """The file's own id is fixed, so an edit would re-publish it with new content.

    `publish` refuses a duplicate id -- correctly -- so a content-blind id would
    mean an edited file could never be seeded at all.
    """
    schema = load_active_schema(schema_path)
    store = FakeReleases()
    outcome = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    assert outcome.configuration_release_id == seeded_release_id(
        schema.configuration_release_id, schema.configuration_checksum
    )
    assert outcome.configuration_release_id != schema.configuration_release_id


@pytest.mark.asyncio
async def test_seeding_twice_from_an_unchanged_file_changes_nothing(schema_path: Path) -> None:
    store = FakeReleases()
    first = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    second = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    assert second.status == "ALREADY_ACTIVE"
    assert store.activations == [first.configuration_release_id]


@pytest.mark.asyncio
async def test_an_operator_release_is_never_overwritten(schema_path: Path) -> None:
    """The failure this must not have: a restart reverting somebody's activation."""

    class OperatorRelease:
        configuration_release_id = "operator-chosen"
        configuration_checksum = "a" * 64

    store = FakeReleases()
    store.published["operator-chosen"] = OperatorRelease()
    store.pointer = "operator-chosen"

    outcome = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    assert outcome.status == "OPERATOR_RELEASE_ACTIVE"
    assert store.pointer == "operator-chosen"
    assert store.activations == [], "seeding must not activate over a live release"


@pytest.mark.asyncio
async def test_a_release_published_earlier_is_activated_rather_than_refused(
    schema_path: Path,
) -> None:
    """Published on an earlier boot, then deactivated. Activating it is still right."""
    schema = load_active_schema(schema_path)
    release_id = seeded_release_id(schema.configuration_release_id, schema.configuration_checksum)
    store = FakeReleases()
    store.published[release_id] = schema  # published, but no pointer

    outcome = await seed_release_from_file(schema_path, store)  # type: ignore[arg-type]
    assert outcome.status == "SEEDED"
    assert store.pointer == release_id
