"""API contract tests for governed seed operations."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import seed
from return_platform.operations.models import SeedStatusView
from return_platform.security.principal import Principal


class FakeSeedCoordinator:
    def __init__(self) -> None:
        self.applied_limits: list[int] = []
        self.deleted_limits: list[int | None] = []

    @staticmethod
    def _status(record_limit: int | None = 1_000) -> SeedStatusView:
        return SeedStatusView(
            version="e2e-v2",
            digest="digest" if record_limit is not None else "",
            ready=record_limit is not None,
            counts={},
            scenarioCounts={},
            validationErrors=[] if record_limit is not None else ["Seed data is absent."],
            requestedRecordLimit=record_limit,
        )

    async def status(self) -> SeedStatusView:
        return self._status()

    async def apply(self, _actor_id: str, **kwargs: Any) -> SeedStatusView:
        record_limit = int(kwargs["record_limit"])
        self.applied_limits.append(record_limit)
        return self._status(record_limit)

    async def delete_all(self, **kwargs: Any) -> SeedStatusView:
        self.deleted_limits.append(kwargs.get("record_limit"))
        return self._status(None)

    async def reset_and_apply(self, _actor_id: str, **kwargs: Any) -> SeedStatusView:
        record_limit = int(kwargs["record_limit"])
        self.applied_limits.append(record_limit)
        return self._status(record_limit)


@pytest.fixture
def seed_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSeedCoordinator]:
    coordinator = FakeSeedCoordinator()
    app = FastAPI()
    app.include_router(seed.router)

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="seed-admin",
            roles=frozenset({"console_admin"}),
        )
        request.state.correlation_id = "seed-api-test"
        return await call_next(request)

    monkeypatch.setattr(seed, "_coordinator", lambda _request: coordinator)
    return TestClient(app), coordinator


def test_apply_uses_requested_record_limit(
    seed_client: tuple[TestClient, FakeSeedCoordinator],
) -> None:
    client, coordinator = seed_client

    response = client.post("/api/v1/seed-data/apply", json={"recordLimit": 2_500})

    assert response.status_code == 200
    assert response.json()["data"]["requestedRecordLimit"] == 2_500
    assert coordinator.applied_limits == [2_500]


def test_record_limit_is_validated(
    seed_client: tuple[TestClient, FakeSeedCoordinator],
) -> None:
    client, _coordinator = seed_client

    assert client.post(
        "/api/v1/seed-data/apply",
        json={"recordLimit": 9},
    ).status_code == 422
    assert client.post(
        "/api/v1/seed-data/apply",
        json={"recordLimit": 1_000_001},
    ).status_code == 422


def test_delete_requires_confirmation_and_is_seed_scoped(
    seed_client: tuple[TestClient, FakeSeedCoordinator],
) -> None:
    client, coordinator = seed_client

    rejected = client.post(
        "/api/v1/seed-data/delete",
        json={"confirmation": "DELETE"},
    )
    accepted = client.post(
        "/api/v1/seed-data/delete",
        json={"confirmation": "DELETE SEED DATA"},
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 200
    assert coordinator.deleted_limits == [1_000]


def test_cancel_marks_a_running_operation_as_cancelling(
    seed_client: tuple[TestClient, FakeSeedCoordinator],
) -> None:
    client, _coordinator = seed_client
    control = seed.SeedOperationControl()
    client.app.state.seed_operation_control = control
    asyncio.run(
        control.begin(
            kind="APPLY",
            record_limit=10_000,
            total_records=34_000,
        )
    )

    response = client.post("/api/v1/seed-data/cancel")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLING"
