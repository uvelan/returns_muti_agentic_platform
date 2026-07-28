from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.data_console.api.operational_generation import (
    get_actor_id,
    get_actor_permissions,
    router,
)
from return_platform.data_platform.operational_generation.adapters.graph_sync import (
    configure_graph_sync,
)
from return_platform.data_platform.operational_generation.adapters.source_mongodb import (
    configure_source_mongodb,
)
from return_platform.data_platform.schema_registry import load_schema_registry


class _Collection:
    async def insert_one(self, _document: object) -> None:
        return None

    async def delete_one(self, _filter: object) -> None:
        return None


class _Database:
    def __getitem__(self, _name: str) -> _Collection:
        return _Collection()


class _MongoClient:
    def __getitem__(self, _name: str) -> _Database:
        return _Database()


class _GraphSyncService:
    async def sync(self, _request: object, *, actor_id: str) -> SimpleNamespace:
        assert actor_id == "operational-generation"
        return SimpleNamespace(status="COMPLETED")

    async def remove_source_mongodb_records(self, _records: object) -> None:
        return None


def test_operational_generation_ui_contract() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_actor_permissions] = lambda: [
        "AI_STUDIO_GENERATE",
        "AI_STUDIO_VALIDATE",
        "AI_STUDIO_PLAN",
        "AI_STUDIO_APPROVE",
        "AI_STUDIO_APPLY_OPERATIONAL",
        "AI_STUDIO_ROLLBACK_OPERATIONAL",
        "AI_STUDIO_VIEW_OPERATIONAL",
    ]
    app.dependency_overrides[get_actor_id] = lambda: "windows-test-admin"
    app.state.resources = SimpleNamespace(
        schema_registry=load_schema_registry(
            Path(__file__).parents[3] / "config" / "schema_registry.yaml"
        )
    )

    configure_source_mongodb(  # type: ignore[arg-type]
        _MongoClient(),
        "return_source",
        app.state.resources.schema_registry,
    )
    configure_graph_sync(_GraphSyncService())  # type: ignore[arg-type]
    try:
        with TestClient(app) as client:
            proposal_response = client.post(
                "/api/v1/data-console/ai-studio/operational/proposals",
                json={
                    "assetIds": ["source.mongodb.customers"],
                    "recordsPerAsset": 1,
                    "seed": 42,
                    "mode": "DETERMINISTIC",
                    "scenarioName": "windows-smoke-test",
                },
            )
            assert proposal_response.status_code == 201
            proposal = proposal_response.json()["data"]
            checksum = proposal["proposal_checksum"]

            validation_response = client.post(
                f"/api/v1/data-console/ai-studio/operational/proposals/{checksum}/validate"
            )
            assert validation_response.status_code == 200
            assert validation_response.json()["data"]["state"] == "VALID"

            plan_response = client.post(
                f"/api/v1/data-console/ai-studio/operational/proposals/{checksum}/plan",
                params={"plan_salt": "windows-smoke-test"},
            )
            assert plan_response.status_code == 200
            plan = plan_response.json()["data"]
            assert plan["proposal_checksum"] == checksum

            approval_response = client.post(
                f"/api/v1/data-console/ai-studio/operational/proposals/{checksum}/approve",
                params={
                    "plan_id": plan["plan_id"],
                    "target_environment": "development",
                },
            )
            assert approval_response.status_code == 200
            approval = approval_response.json()["data"]

            apply_response = client.post(
                f"/api/v1/data-console/ai-studio/operational/proposals/{checksum}/apply",
                params={
                    "plan_id": plan["plan_id"],
                    "approval_id": approval["approval_id"],
                    "target_environment": "development",
                },
            )
            assert apply_response.status_code == 200
            run = apply_response.json()["data"]
            assert run["state"] == "APPLIED"

            rollback_response = client.post(
                f"/api/v1/data-console/ai-studio/operational/runs/{run['run_id']}/rollback",
                params={"plan_id": plan["plan_id"]},
            )
            assert rollback_response.status_code == 200
            assert rollback_response.json()["data"]["state"] == "ROLLED_BACK"
    finally:
        configure_graph_sync(None)
        configure_source_mongodb(None)
