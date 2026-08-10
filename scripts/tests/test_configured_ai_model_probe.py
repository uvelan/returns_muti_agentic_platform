from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr


def _load_probe_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "probe_configured_ai_models.py"
    spec = importlib.util.spec_from_file_location("configured_ai_model_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(
        self,
        *,
        catalogs: dict[str, dict[str, Any]],
        statuses: dict[str, int],
        credential_statuses: dict[str, int] | None = None,
    ) -> None:
        self._catalogs = catalogs
        self._statuses = statuses
        self._credential_statuses = credential_statuses or {}

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _Response:
        headers = kwargs.get("headers")
        assert isinstance(headers, dict)
        credential = str(
            headers.get("x-goog-api-key")
            or str(headers.get("Authorization", "")).removeprefix("Bearer ")
        )
        status = self._credential_statuses.get(credential, 200)
        return _Response(status, self._catalogs[url] if status == 200 else {})

    async def post(
        self, url: str, *, json: dict[str, Any], **_kwargs: object
    ) -> _Response:
        model = str(json.get("model") or url.split("/models/", 1)[1].split(":", 1)[0])
        return _Response(self._statuses[model], {})


class _Settings:
    resolved_google_api_keys = (SecretStr("google-key"),)
    google_lightweight_models = ("g-light-1", "g-light-2")
    resolved_google_standard_models = ("g-standard-1", "g-standard-2")
    google_base_url = "https://google.example/v1beta"
    resolved_nvidia_api_keys = (SecretStr("nvidia-key"),)
    nvidia_lightweight_models = ("n-light-1", "n-light-2", "n-light-3")
    resolved_nvidia_standard_models = ("n-standard-1", "n-standard-2")
    nvidia_base_url = "https://nvidia.example/v1"


class _DuplicateSettings(_Settings):
    resolved_google_standard_models = ("g-light-2", "g-standard-1")


class _MultiKeySettings(_Settings):
    resolved_google_api_keys = (SecretStr("google-key-a"), SecretStr("google-key-b"))
    resolved_nvidia_api_keys = (SecretStr("nvidia-key-a"), SecretStr("nvidia-key-b"))


@pytest.mark.asyncio
async def test_probe_verifies_exact_configured_model_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_probe_module()
    google_models = [
        "g-light-1",
        "g-light-2",
        "g-standard-1",
        "g-standard-2",
        "unconfigured-google-model",
    ]
    nvidia_models = [
        "n-light-1",
        "n-light-2",
        "n-light-3",
        "n-standard-1",
        "n-standard-2",
        "unconfigured-nvidia-model",
    ]
    client = _Client(
        catalogs={
            "https://google.example/v1beta/models": {
                "models": [
                    {
                        "name": f"models/{model}",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                    for model in google_models
                ]
            },
            "https://nvidia.example/v1/models": {
                "data": [{"id": model} for model in nvidia_models]
            },
        },
        statuses={model: 200 for model in [*google_models, *nvidia_models]},
    )
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: client)

    google = await module._google(_Settings())
    nvidia = await module._nvidia(_Settings())

    assert google["allConfiguredModelsWorking"] is True
    assert google["configuredModelCount"] == 4
    assert {item["model"] for item in google["workingModels"]} == set(google_models[:4])
    assert nvidia["allConfiguredModelsWorking"] is True
    assert nvidia["configuredModelCount"] == 5
    assert {item["model"] for item in nvidia["workingModels"]} == set(nvidia_models[:5])


@pytest.mark.asyncio
async def test_probe_fails_closed_when_configured_model_is_missing_or_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_probe_module()
    client = _Client(
        catalogs={
            "https://google.example/v1beta/models": {
                "models": [
                    {
                        "name": "models/g-light-1",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/g-light-2",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/g-standard-1",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            },
            "https://nvidia.example/v1/models": {
                "data": [
                    {"id": model}
                    for model in (
                        "n-light-1",
                        "n-light-2",
                        "n-light-3",
                        "n-standard-1",
                        "n-standard-2",
                    )
                ]
            },
        },
        statuses={
            "g-light-1": 200,
            "g-light-2": 200,
            "g-standard-1": 503,
            "n-light-1": 200,
            "n-light-2": 200,
            "n-light-3": 200,
            "n-standard-1": 200,
            "n-standard-2": 429,
        },
    )
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: client)

    google = await module._google(_Settings())
    nvidia = await module._nvidia(_Settings())

    assert google["allConfiguredModelsWorking"] is False
    assert any(item["catalogPresent"] is False for item in google["modelResults"])
    assert any(item["generationStatus"] == 503 for item in google["modelResults"])
    assert nvidia["allConfiguredModelsWorking"] is False
    assert any(item["generationStatus"] == 429 for item in nvidia["modelResults"])


@pytest.mark.asyncio
async def test_probe_fails_closed_for_duplicate_tier_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_probe_module()
    client = _Client(
        catalogs={
            "https://google.example/v1beta/models": {
                "models": [
                    {
                        "name": f"models/{model}",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                    for model in ("g-light-1", "g-light-2", "g-standard-1")
                ]
            },
            "https://nvidia.example/v1/models": {"data": []},
        },
        statuses={
            "g-light-1": 200,
            "g-light-2": 200,
            "g-standard-1": 200,
        },
    )
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: client)

    google = await module._google(_DuplicateSettings())

    assert google["configuredModelCount"] == 3
    assert google["duplicateConfiguredModels"] == ["g-light-2"]
    assert google["allConfiguredModelsWorking"] is False


@pytest.mark.asyncio
async def test_probe_checks_every_configured_credential_without_exposing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_probe_module()
    google_models = ("g-light-1", "g-light-2", "g-standard-1", "g-standard-2")
    nvidia_models = (
        "n-light-1",
        "n-light-2",
        "n-light-3",
        "n-standard-1",
        "n-standard-2",
    )
    client = _Client(
        catalogs={
            "https://google.example/v1beta/models": {
                "models": [
                    {
                        "name": f"models/{model}",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                    for model in google_models
                ]
            },
            "https://nvidia.example/v1/models": {
                "data": [{"id": model} for model in nvidia_models]
            },
        },
        statuses={model: 200 for model in (*google_models, *nvidia_models)},
        credential_statuses={
            "google-key-a": 200,
            "google-key-b": 401,
            "nvidia-key-a": 200,
            "nvidia-key-b": 429,
        },
    )
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: client)

    google = await module._google(_MultiKeySettings())
    nvidia = await module._nvidia(_MultiKeySettings())
    serialized = str({"GOOGLE": google, "NVIDIA": nvidia})

    assert google["credentialCount"] == 2
    assert google["allConfiguredCredentialsWorking"] is False
    assert nvidia["credentialCount"] == 2
    assert nvidia["allConfiguredCredentialsWorking"] is False
    assert "google-key-a" not in serialized
    assert "google-key-b" not in serialized
    assert "nvidia-key-a" not in serialized
    assert "nvidia-key-b" not in serialized
