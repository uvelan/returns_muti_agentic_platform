#!/usr/bin/env python3
"""Dependency-light Stage 4M source and behavior validation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def require(path: str) -> None:
    target = ROOT / path
    if not target.exists() or target.stat().st_size == 0:
        raise AssertionError(f"Missing required artifact: {path}")


async def behavior() -> dict[str, object]:
    import sys

    sys.path.insert(0, str(BACKEND / "src"))
    from return_platform.configuration.settings import Settings
    from return_platform.dependency_simulation.configuration import (
        load_dependency_simulation_configuration,
    )
    from return_platform.dependency_simulation.models import SimulationOperationRequest
    from return_platform.dependency_simulation.repository import (
        MemorySimulationRepository,
    )
    from return_platform.dependency_simulation.service import (
        DependencySimulationService,
    )

    repository = MemorySimulationRepository()
    settings = Settings.model_construct(
        environment="test",
        google_api_key=None,
        nvidia_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
        ollama_model=None,
    )
    service = DependencySimulationService(
        repository,
        settings,
        load_dependency_simulation_configuration(
            BACKEND / "config" / "dependency_simulation.yaml"
        ),
    )
    session = "STAGE4M-E2E"
    sequence = [
        ("OMC", "CREATE_RMA", {"items": [{"quantity": 1}]}),
        ("PARCEL", "CREATE_RETURN_LABEL", {"handlingUnitId": "HU-001"}),
        ("PARCEL", "ADVANCE_TRACKING", {"targetStatus": "PACKAGE_READY"}),
        ("PARCEL", "ADVANCE_TRACKING", {"targetStatus": "CARRIER_ACCEPTED"}),
        ("LSI", "RECORD_RECEIPT", {"handlingUnitId": "HU-001"}),
        ("LSI", "ASSIGN_LICENSE_PLATE", {"handlingUnitId": "HU-001"}),
        ("OMC", "SET_CUSTOMER_RESOLUTION", {"customerResolution": "REFUNDED"}),
        ("LSI", "SET_PRODUCT_RESOLUTION", {"productResolution": "RTV"}),
        ("LSI", "COMPLETE_WAREHOUSE_PROCESSING", {}),
        ("LSI", "CREATE_RGA", {}),
        ("LSI", "RECORD_VENDOR_CREDIT", {}),
    ]
    operations = []
    for index, (dependency, operation, payload) in enumerate(sequence, start=1):
        item = await service.execute(
            SimulationOperationRequest(
                dependency=dependency,
                operation=operation,
                sessionId=session,
                idempotencyKey=f"{session}:{index}:{operation}",
                payload=payload,
                useAiNarrative=True,
                signalWorkflow=False,
            )
        )
        assert item.status.value == "CONFIRMED", (operation, item.errorCode)
        operations.append(item)
    assert operations[0].externalReference and operations[
        0
    ].externalReference.startswith("2SIM")
    assert operations[1].simulatedState == "LABEL_CREATED"
    assert operations[2].simulatedState == "PACKAGE_READY"
    assert operations[3].simulatedState == "CARRIER_ACCEPTED"
    assert operations[9].externalReference and operations[
        9
    ].externalReference.startswith("RGA-SIM-")
    assert operations[10].simulatedState == "VENDOR_CREDIT_CONFIRMED"
    ai = await repository.ai_summary()
    assert ai.requestCount == len(sequence)
    assert ai.fallbackCount == len(sequence)
    return {
        "operations": len(operations),
        "aiRequests": ai.requestCount,
        "fallbacks": ai.fallbackCount,
        "tokens": ai.totalTokens,
    }


def main() -> None:
    required = [
        "backend/config/dependency_simulation.yaml",
        "backend/src/return_platform/dependency_simulation/service.py",
        "backend/src/return_platform/dependency_simulation/ai.py",
        "backend/src/return_platform/api/dependency_simulator.py",
        "scripts/run_stage4m_simulated_e2e.sh",
        "docs/plans/STAGE_4M_DEPENDENCY_SIMULATION_IMPLEMENTATION_PLAN.md",
    ]
    for item in required:
        require(item)
    # Six dependency-simulator pages and their five routes were asserted here
    # until Wave F4 deleted the legacy frontend. The simulator's *backend* is
    # untouched and still checked above and below -- its configuration, service,
    # AI adapter, API module, and the production guard. Only the UI is gone, so
    # only the UI assertions went.
    settings = (
        ROOT / "backend/src/return_platform/configuration/settings.py"
    ).read_text()
    assert "External dependency simulation is forbidden in production." in settings
    behavior_result = asyncio.run(behavior())
    result = {
        "stage": "4M",
        "status": "PASSED",
        "validationLevel": "SOURCE_VALIDATED",
        **behavior_result,
        "checks": len(required) + 7,
    }
    evidence = (
        ROOT / "docs/evidence/stage4m_dependency_simulation/validation_summary.json"
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
