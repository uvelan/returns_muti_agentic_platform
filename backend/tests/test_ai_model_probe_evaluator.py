from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "evaluate_ai_model_probe.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe_gate", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_module()


def model(name: str, tier: str, status: int | str) -> dict[str, Any]:
    return {
        "model": name,
        "tier": tier,
        "catalogPresent": True,
        "generationStatus": status,
    }


def provider(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "configured": True,
        "catalogStatus": 200,
        "allConfiguredCredentialsWorking": True,
        "configuredModelCount": len(results),
        "duplicateConfiguredModels": [],
        "modelResults": results,
    }


def payload(
    google: list[dict[str, Any]],
    nvidia: list[dict[str, Any]],
) -> dict[str, Any]:
    return {"GOOGLE": provider(google), "NVIDIA": provider(nvidia)}


def healthy(provider_name: str) -> list[dict[str, Any]]:
    return [
        model(f"{provider_name}-light", "LIGHTWEIGHT", 200),
        model(f"{provider_name}-standard", "STANDARD", 200),
    ]


def test_all_healthy_passes() -> None:
    result = GATE.evaluate(payload(healthy("google"), healthy("nvidia")))
    assert result["accepted"] is True
    assert result["overallStatus"] == "PASS"


def test_google_429_with_nvidia_coverage_passes_with_warning() -> None:
    result = GATE.evaluate(
        payload(
            [
                model("google-light", "LIGHTWEIGHT", 429),
                model("google-standard", "STANDARD", 429),
            ],
            healthy("nvidia"),
        )
    )
    assert result["accepted"] is True
    assert result["overallStatus"] == "PASS_WITH_WARNING"
    assert result["providerResults"]["GOOGLE"]["status"] == "DEGRADED_RATE_LIMITED"


def test_every_provider_rate_limited_fails() -> None:
    result = GATE.evaluate(
        payload(
            [
                model("google-light", "LIGHTWEIGHT", 429),
                model("google-standard", "STANDARD", 429),
            ],
            [
                model("nvidia-light", "LIGHTWEIGHT", 429),
                model("nvidia-standard", "STANDARD", 429),
            ],
        )
    )
    assert result["accepted"] is False
    assert result["overallStatus"] == "FAIL"


def test_unresolved_http_error_fails() -> None:
    result = GATE.evaluate(
        payload(
            healthy("google"),
            [
                model("nvidia-light", "LIGHTWEIGHT", 200),
                model("nvidia-standard", "STANDARD", "HTTP_ERROR"),
            ],
        )
    )
    assert result["accepted"] is False
    assert any("HTTP_ERROR" in item for item in result["hardFailures"])


def test_catalog_absence_fails() -> None:
    google = healthy("google")
    google[0]["catalogPresent"] = False
    result = GATE.evaluate(payload(google, healthy("nvidia")))
    assert result["accepted"] is False
    assert any("absent from catalog" in item for item in result["hardFailures"])


def test_missing_standard_coverage_fails() -> None:
    result = GATE.evaluate(
        payload(
            [
                model("google-light", "LIGHTWEIGHT", 200),
                model("google-standard", "STANDARD", 429),
            ],
            [
                model("nvidia-light", "LIGHTWEIGHT", 200),
                model("nvidia-standard", "STANDARD", 429),
            ],
        )
    )
    assert result["accepted"] is False
    assert any("STANDARD" in item for item in result["hardFailures"])
