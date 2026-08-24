"""`/api/schema-releases/active/document`: editing the running schema over HTTP.

Editing the schema meant editing a YAML file on the server and restarting. That
is not something an operator can do, it leaves no record of who changed what,
and it has a trap in it: `load_active_schema` refuses a document whose checksum
does not match its content, so a hand edit that forgot to reseal produced a
platform that would not start.

These cover the three things this surface does that a hand edit does not get --
the checksum is recomputed, the edit is validated before it is published, and a
concurrent edit is refused rather than silently discarding somebody's work.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import schema_releases as module
from return_platform.api.schema_releases import router
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.release_migration import MigrationPlan, plan_migration
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.security import roles as r
from return_platform.security.principal import Principal

BASELINE = load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)

DOCUMENT_URL = "/api/schema-releases/active/document"


class InMemoryReleases:
    """The store's contract, with the real planner behind activation."""

    def __init__(self) -> None:
        self.releases: dict[str, ActiveSchema] = {}
        self.active_id: str | None = None
        self.published_by: list[str] = []

    async def read(self, configuration_release_id: str) -> ActiveSchema | None:
        return self.releases.get(configuration_release_id)

    async def active(self) -> ActiveSchema | None:
        return None if self.active_id is None else self.releases[self.active_id]

    async def publish(self, schema: ActiveSchema, *, published_by: str) -> None:
        self.releases[schema.configuration_release_id] = schema
        self.published_by.append(published_by)

    async def preview_activation(self, configuration_release_id: str) -> MigrationPlan:
        target = await self.read(configuration_release_id)
        if target is None:
            raise LookupError(configuration_release_id)
        return plan_migration(await self.active(), target)

    async def activate(self, configuration_release_id: str) -> MigrationPlan:
        plan = await self.preview_activation(configuration_release_id)
        self.active_id = configuration_release_id
        return plan


@pytest.fixture
def store() -> InMemoryReleases:
    made = InMemoryReleases()
    made.releases[BASELINE.configuration_release_id] = BASELINE
    made.active_id = BASELINE.configuration_release_id
    return made


@pytest.fixture(autouse=True)
def _stub(monkeypatch: pytest.MonkeyPatch, store: InMemoryReleases) -> None:
    monkeypatch.setattr(module, "_store", lambda request: store)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()

    class _Settings:
        dynamic_knowledge_schema_path = DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH

    app.state.settings = _Settings()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="operator-1", roles=frozenset({r.CONSOLE_ADMIN})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as made:
        yield made


def _edited(version: str = "2099.01.01") -> dict[str, Any]:
    """The active document with a harmless change, so the checksum differs."""
    document = BASELINE.model_dump(mode="json")
    document["schema_version"] = version
    return document


def test_the_document_served_is_the_one_the_runtime_is_running(client: TestClient) -> None:
    body = client.get(DOCUMENT_URL).json()["data"]
    assert body["configurationReleaseId"] == BASELINE.configuration_release_id
    assert body["fromFile"] is False
    assert body["document"]["schema_version"] == BASELINE.schema_version


def test_an_edit_is_published_and_becomes_active(
    client: TestClient, store: InMemoryReleases
) -> None:
    response = client.put(
        DOCUMENT_URL,
        json={"document": _edited(), "baseChecksum": BASELINE.configuration_checksum},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert store.active_id == body["configurationReleaseId"]
    assert body["document"]["schema_version"] == "2099.01.01"


def test_the_checksum_is_recomputed_rather_than_trusted(client: TestClient) -> None:
    """A hand edit that forgot to reseal produced a platform that would not start.

    A stale checksum in the submitted document must not survive into the
    published release.
    """
    document = _edited()
    document["configuration_checksum"] = "0" * 64
    body = client.put(
        DOCUMENT_URL,
        json={"document": document, "baseChecksum": BASELINE.configuration_checksum},
    ).json()["data"]

    expected = dict(body["document"])
    expected.pop("configuration_checksum")
    assert body["configurationChecksum"] == sha256_digest(expected)
    assert body["configurationChecksum"] != "0" * 64


def test_a_concurrent_edit_is_refused_rather_than_overwriting(client: TestClient) -> None:
    """Two operators editing at once must not have the second discard the first."""
    response = client.put(
        DOCUMENT_URL, json={"document": _edited(), "baseChecksum": "a stale checksum"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SCHEMA_CHANGED_UNDER_EDIT"


def test_an_unchanged_document_is_not_published_as_a_release(client: TestClient) -> None:
    """A release per press of the save button would make the list unreadable."""
    response = client.put(
        DOCUMENT_URL,
        json={
            "document": BASELINE.model_dump(mode="json"),
            "baseChecksum": BASELINE.configuration_checksum,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SCHEMA_UNCHANGED"


def test_a_malformed_schema_is_refused_before_it_becomes_a_release(
    client: TestClient, store: InMemoryReleases
) -> None:
    """The alternative is a published release that nothing can load."""
    document = _edited()
    document["entities"] = "not a mapping of entities"
    response = client.put(
        DOCUMENT_URL,
        json={"document": document, "baseChecksum": BASELINE.configuration_checksum},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SCHEMA_INVALID"
    assert store.active_id == BASELINE.configuration_release_id, "nothing was activated"


def test_publishing_without_activating_leaves_the_runtime_alone(
    client: TestClient, store: InMemoryReleases
) -> None:
    """Publishing and activating are separate decisions everywhere else here too."""
    client.put(
        DOCUMENT_URL,
        json={
            "document": _edited(),
            "baseChecksum": BASELINE.configuration_checksum,
            "activate": False,
        },
    )
    assert store.active_id == BASELINE.configuration_release_id
    assert len(store.releases) == 2, "it was published, just not activated"


def test_the_editor_is_recorded_as_the_publisher(
    client: TestClient, store: InMemoryReleases
) -> None:
    """A YAML edit on a server leaves no record of who changed what."""
    client.put(
        DOCUMENT_URL,
        json={"document": _edited(), "baseChecksum": BASELINE.configuration_checksum},
    )
    assert store.published_by == ["operator-1"]


def test_the_published_release_verifies_against_its_own_content(
    client: TestClient, store: InMemoryReleases
) -> None:
    """The invariant `load_active_schema` enforces, applied to what was stored.

    `operators` and `allowed_entity_ids` are set-typed, and `model_dump`
    renders a set as a list in set-iteration order -- which is not the order
    the same set yields after being rebuilt from that list. A checksum taken
    over the wrong dump produces a release that loads nowhere: the checksum
    guard rejects it, and the platform refuses to start on it.
    """
    body = client.put(
        DOCUMENT_URL,
        json={"document": _edited(), "baseChecksum": BASELINE.configuration_checksum},
    ).json()["data"]

    published = store.releases[body["configurationReleaseId"]]
    stored = published.model_dump(mode="json")
    supplied = stored.pop("configuration_checksum")

    assert supplied == sha256_digest(stored), (
        "the stored release does not hash to its own checksum, so loading it would fail"
    )


def test_dumping_a_schema_is_deterministic() -> None:
    """What every checksum in the release mechanism rests on.

    The set-typed fields (`operators`, `allowed_entity_ids`, and the permission
    sets) used to dump in set-iteration order, which depends on the hash values
    and insertion history of that particular set object. Dumping, re-parsing
    and dumping again did not settle. So a release published from one dump and
    verified against another did not match its own checksum -- intermittently,
    depending on which order the sets happened to come out in.
    """
    first = BASELINE.model_dump(mode="json")
    second = ActiveSchema.model_validate(first).model_dump(mode="json")
    third = ActiveSchema.model_validate(second).model_dump(mode="json")

    assert first == second == third
