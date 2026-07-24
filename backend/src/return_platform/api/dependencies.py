"""Operational dependency and runtime-readiness evidence APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_read_roles
from return_platform.data_console.infrastructure.probes import (
    probe_mongodb,
    probe_neo4j,
    probe_source_mongodb,
    probe_sqlserver,
    probe_temporal,
    probe_valkey,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.shared.contracts import APIResponse, DependencyProbeResult, ResponseMeta

router = APIRouter(prefix="/api/v1/system/dependencies", tags=["Operational Readiness"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _probe_card(
    dependency_id: str,
    name: str,
    category: str,
    result: DependencyProbeResult,
) -> dict[str, Any]:
    status = result.status.value
    message = result.safe_message or (
        "Dependency probe completed successfully."
        if status == "HEALTHY"
        else "Dependency probe did not establish readiness."
    )
    return {
        "id": dependency_id,
        "name": name,
        "category": category,
        "status": status,
        "message": message,
        "checkedAt": result.checked_at,
        "details": {
            "latencyMs": result.latency_ms,
            "errorCode": result.error_code.value if result.error_code is not None else None,
            "validationLevel": "LIVE_PROBE",
        },
    }


async def _run_core_probes(request: Request) -> list[dict[str, Any]]:
    specifications: tuple[
        tuple[str, str, str, Callable[[Request], Awaitable[DependencyProbeResult]]], ...
    ] = (
        ("mongodb", "Platform MongoDB", "DATABASE", probe_mongodb),
        ("source-mongodb", "Source MongoDB", "SOURCE", probe_source_mongodb),
        ("sqlserver", "SQL Server", "DATABASE", probe_sqlserver),
        ("neo4j", "Neo4j", "GRAPH", probe_neo4j),
        ("valkey", "Valkey", "EVENT_TRANSPORT", probe_valkey),
        ("temporal", "Temporal", "WORKFLOW", probe_temporal),
    )
    results = await asyncio.gather(*(probe(request) for _, _, _, probe in specifications))
    return [
        _probe_card(dependency_id, name, category, result)
        for (dependency_id, name, category, _), result in zip(specifications, results, strict=True)
    ]


async def _cards(request: Request) -> list[dict[str, Any]]:
    repository = resolve_operational_repository(request)
    settings = request.app.state.settings
    now = datetime.now(UTC)
    cards = await _run_core_probes(request)

    for worker in (
        "return-workflow-worker",
        "return-orchestrator",
        "outbox-publisher",
        "data-job-worker",
    ):
        heartbeat = await repository.get_heartbeat(worker)
        last_seen = heartbeat.get("lastSeenAt") if heartbeat else None
        healthy = (
            isinstance(last_seen, datetime)
            and (now - last_seen.astimezone(UTC)).total_seconds()
            <= settings.worker_readiness_ttl_seconds
        )
        cards.append(
            {
                "id": worker,
                "name": worker.replace("-", " ").title(),
                "category": "WORKER",
                "status": "HEALTHY" if healthy else "UNAVAILABLE",
                "message": (
                    "Worker heartbeat is current."
                    if healthy
                    else "Worker heartbeat is missing or stale."
                ),
                "checkedAt": now,
                "details": {
                    **(heartbeat or {}),
                    "readinessTtlSeconds": settings.worker_readiness_ttl_seconds,
                    "validationLevel": "DURABLE_HEARTBEAT",
                },
            }
        )

    unpublished = await repository.events.count_documents({"publishedAt": None})
    oldest = await repository.events.find_one(
        {"publishedAt": None},
        sort=[("occurredAt", 1)],
        projection={"occurredAt": 1},
    )
    oldest_at = oldest.get("occurredAt") if oldest else None
    oldest_age_seconds = (
        int((now - oldest_at.astimezone(UTC)).total_seconds())
        if isinstance(oldest_at, datetime)
        else 0
    )
    outbox_healthy = unpublished == 0 or oldest_age_seconds <= settings.worker_readiness_ttl_seconds * 2
    cards.append(
        {
            "id": "operational-outbox",
            "name": "Operational Event Outbox",
            "category": "EVENT_TRANSPORT",
            "status": "HEALTHY" if outbox_healthy else "DEGRADED",
            "message": (
                "Operational events are published or within the bounded delivery window."
                if outbox_healthy
                else "Unpublished operational events exceed the delivery-age threshold."
            ),
            "checkedAt": now,
            "details": {
                "unpublishedCount": unpublished,
                "oldestUnpublishedAgeSeconds": oldest_age_seconds,
                "validationLevel": "DURABLE_QUEUE_EVIDENCE",
            },
        }
    )

    seed = await repository.seed_status()
    cards.append(
        {
            "id": "seed-data",
            "name": "Seed Data Readiness",
            "category": "DATA",
            "status": "HEALTHY" if seed.ready else "UNAVAILABLE",
            "message": (
                "Canonical E2E scenarios and digest are ready."
                if seed.ready
                else "Canonical E2E source data is incomplete or has drifted."
            ),
            "checkedAt": now,
            "details": {**seed.model_dump(mode="json"), "validationLevel": "DIGEST_VALIDATED"},
        }
    )

    ai_settings = await repository.get_ai_settings()
    providers = {
        "GOOGLE": bool(settings.google_api_key and settings.google_model),
        "NVIDIA": bool(settings.nvidia_api_key and settings.nvidia_model),
        "OPENAI": bool(settings.openai_api_key and settings.openai_model),
        "ANTHROPIC": bool(settings.anthropic_api_key and settings.anthropic_model),
        "OLLAMA": bool(settings.ollama_model),
        "SIMULATOR": settings.environment in {"development", "test"},
    }
    for provider in ai_settings.providerOrder:
        configured = providers.get(provider, False)
        is_simulator = provider == "SIMULATOR"
        cards.append(
            {
                "id": f"ai-{provider.lower()}",
                "name": f"AI Provider {provider}",
                "category": "AI_PROVIDER",
                "status": "HEALTHY" if configured and is_simulator else ("UNKNOWN" if configured else "UNAVAILABLE"),
                "message": (
                    "Deterministic simulator is available in this non-production environment."
                    if configured and is_simulator
                    else (
                        "Provider is configured; use the AI comparison/replay screen for live validation."
                        if configured
                        else "Provider credentials or model are not configured."
                    )
                ),
                "checkedAt": now,
                "details": {
                    "provider": provider,
                    "configured": configured,
                    "interceptMode": ai_settings.interceptMode,
                    "validationLevel": "CONFIGURED_NOT_CALLED" if configured and not is_simulator else "LOCAL_POLICY_CHECK",
                },
            }
        )
    return cards


@router.get("", response_model=APIResponse[list[dict[str, Any]]])
async def list_dependencies(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    return APIResponse(data=await _cards(request), meta=_meta(request))


@router.get("/{dependency_id}", response_model=APIResponse[dict[str, Any]])
async def get_dependency(
    request: Request,
    dependency_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    card = next((item for item in await _cards(request) if item["id"] == dependency_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail="Dependency not found")
    return APIResponse(data=card, meta=_meta(request))
