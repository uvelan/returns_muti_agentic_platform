"""`/api/schema-releases`: what is published, what is live, and what a flip costs.

Over real HTTP against an in-memory store that computes plans with the real
planner -- a stub that fabricated plans would prove routing and nothing else.
What is under test is the router's contract: that a preview writes nothing, that
activation returns the migration it committed to rather than an acknowledgement,
and that an unpublished release is a 404 rather than a pointer at nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import schema_releases as module
from return_platform.api.schema_releases import router
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_migration import MigrationPlan, plan_migration
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.security import roles as r
from return_platform.security.principal import Principal

BASELINE = load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _release(release_id: str) -> ActiveSchema:
    return BASELINE.model_copy(update={"configuration_release_id": release_id})


class InMemoryReleases:
    """The store's contract, with the real planner behind it."""

    def __init__(self) -> None:
        self.releases: dict[str, ActiveSchema] = {}
        self.active_id: str | None = None
        self.recorded: list[MigrationPlan] = []

    def publish(self, schema: ActiveSchema) -> None:
        self.releases[schema.configuration_release_id] = schema

    async def read(self, configuration_release_id: str) -> ActiveSchema | None:
        return self.releases.get(configuration_release_id)

    async def active(self) -> ActiveSchema | None:
        return None if self.active_id is None else self.releases[self.active_id]

    async def list_published(self, *, limit: int = 50) -> list[dict[str, object]]:
        return [
            {"configurationReleaseId": key, "publishedBy": "analyst-1"}
            for key in sorted(self.releases)
        ][:limit]

    async def preview_activation(self, configuration_release_id: str) -> MigrationPlan:
        target = await self.read(configuration_release_id)
        if target is None:
            raise LookupError(f"release {configuration_release_id!r} has not been published")
        return plan_migration(await self.active(), target)

    async def activate(self, configuration_release_id: str) -> MigrationPlan:
        plan = await self.preview_activation(configuration_release_id)
        self.recorded.append(plan)
        self.active_id = configuration_release_id
        return plan


@pytest.fixture
def store() -> InMemoryReleases:
    made = InMemoryReleases()
    made.publish(_release("release_one"))
    made.publish(_release("release_two"))
    return made


@pytest.fixture(autouse=True)
def _stub(monkeypatch: pytest.MonkeyPatch, store: InMemoryReleases) -> None:
    monkeypatch.setattr(module, "_store", lambda request: store)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()

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


def test_the_list_says_which_release_is_live(client: TestClient) -> None:
    """Published and live are separate decisions, and this is the only place an
    operator sees both."""
    client.post("/api/schema-releases/release_one/activate")

    body = client.get("/api/schema-releases").json()["data"]

    assert body["activeReleaseId"] == "release_one"
    assert {row["configurationReleaseId"]: row["active"] for row in body["releases"]} == {
        "release_one": True,
        "release_two": False,
    }


def test_the_first_activation_is_planned_as_a_build(client: TestClient) -> None:
    plan = client.get("/api/schema-releases/release_one/migration-plan").json()["data"]

    assert plan["strategy"] == "FULL_REBUILD"
    assert plan["from_release_id"] is None
    assert plan["rebuild_reasons"]


def test_a_preview_records_nothing(client: TestClient, store: InMemoryReleases) -> None:
    """Deciding whether a change is safe must not half-perform it."""
    client.get("/api/schema-releases/release_two/migration-plan")

    assert store.recorded == []
    assert store.active_id is None


def test_activation_returns_the_migration_it_committed_to(
    client: TestClient, store: InMemoryReleases
) -> None:
    """Not an acknowledgement. Whether a rebuild is now owed is the consequence
    of the act, and an operator who has to go and ask elsewhere will not."""
    client.post("/api/schema-releases/release_one/activate")

    response = client.post("/api/schema-releases/release_two/activate")

    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert plan["from_release_id"] == "release_one"
    assert plan["to_release_id"] == "release_two"
    # Same content, same shape: moving between two identical releases is not a
    # reason to rebuild anything.
    assert plan["strategy"] == "NO_CHANGE"
    assert [item.to_release_id for item in store.recorded] == ["release_one", "release_two"]


def test_a_release_nobody_published_cannot_be_planned_or_activated(client: TestClient) -> None:
    """The alternative is a pointer at nothing and a runtime quietly on the file."""
    assert client.get("/api/schema-releases/never_cut/migration-plan").status_code == 404
    refused = client.post("/api/schema-releases/never_cut/activate")
    assert refused.status_code == 404
    assert refused.json()["detail"]["code"] == "UNKNOWN_RELEASE"
