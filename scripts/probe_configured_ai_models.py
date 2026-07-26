#!/usr/bin/env python3
"""Safely list provider catalogs and minimally probe every configured model.

The script never prints credentials, request headers, or provider response bodies.
It is intended for operator use before updating the model pools in `.env.example`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any

import httpx
from return_platform.configuration.settings import Settings


def _configured_models(
    lightweight: Iterable[str], standard: Iterable[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    configured: list[tuple[str, str]] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for tier, values in (("LIGHTWEIGHT", lightweight), ("STANDARD", standard)):
        for model in values:
            if not model:
                continue
            if model in seen:
                if model not in duplicates:
                    duplicates.append(model)
                continue
            seen.add(model)
            configured.append((tier, model))
    return configured, duplicates


async def _google(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_google_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}
    async with httpx.AsyncClient(timeout=20) as client:
        credential_results: list[dict[str, Any]] = []
        catalog_payload: dict[str, Any] | None = None
        selected_headers: dict[str, str] | None = None
        for index, secret in enumerate(keys, 1):
            headers = {
                "x-goog-api-key": secret.get_secret_value(),
                "Content-Type": "application/json",
            }
            try:
                response = await client.get(f"{settings.google_base_url}/models", headers=headers)
                status: int | str = response.status_code
            except httpx.HTTPError:
                status = "HTTP_ERROR"
                response = None
            credential_results.append(
                {"credentialSafeId": f"google-key-{index}", "catalogStatus": status}
            )
            if status == 200 and catalog_payload is None and response is not None:
                catalog_payload = response.json()
                selected_headers = headers

        all_credentials_working = all(item["catalogStatus"] == 200 for item in credential_results)
        if catalog_payload is None or selected_headers is None:
            return {
                "configured": True,
                "catalogStatus": credential_results[0]["catalogStatus"],
                "credentialCount": len(keys),
                "allConfiguredCredentialsWorking": False,
                "credentialCatalogResults": credential_results,
                "workingModels": [],
            }
        catalog_models = [
            str(item.get("name", "")).removeprefix("models/")
            for item in catalog_payload.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        ]
        configured, duplicates = _configured_models(
            settings.google_lightweight_models,
            settings.resolved_google_standard_models,
        )
        working: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        for tier, model in configured:
            if model not in catalog_models:
                results.append(
                    {
                        "tier": tier,
                        "model": model,
                        "catalogPresent": False,
                        "generationStatus": None,
                    }
                )
                continue
            try:
                probe = await client.post(
                    f"{settings.google_base_url}/models/{model}:generateContent",
                    headers=selected_headers,
                    json={
                        "contents": [{"role": "user", "parts": [{"text": "Reply OK"}]}],
                        "generationConfig": {
                            "temperature": 0,
                            "maxOutputTokens": 8,
                        },
                    },
                )
                status = probe.status_code
            except httpx.HTTPError:
                status = "HTTP_ERROR"
            results.append(
                {
                    "tier": tier,
                    "model": model,
                    "catalogPresent": True,
                    "generationStatus": status,
                }
            )
            if status == 200:
                working.append({"tier": tier, "model": model})
        return {
            "configured": True,
            "catalogStatus": 200,
            "credentialCount": len(keys),
            "allConfiguredCredentialsWorking": all_credentials_working,
            "credentialCatalogResults": credential_results,
            "catalogModelCount": len(catalog_models),
            "configuredModelCount": len(configured),
            "duplicateConfiguredModels": duplicates,
            "allConfiguredModelsWorking": (not duplicates and len(working) == len(configured)),
            "modelResults": results,
            "workingModels": working,
        }


async def _nvidia(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_nvidia_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}
    async with httpx.AsyncClient(timeout=30) as client:
        credential_results: list[dict[str, Any]] = []
        catalog_payload: dict[str, Any] | None = None
        selected_headers: dict[str, str] | None = None
        for index, secret in enumerate(keys, 1):
            headers = {
                "Authorization": f"Bearer {secret.get_secret_value()}",
                "Content-Type": "application/json",
            }
            try:
                response = await client.get(f"{settings.nvidia_base_url}/models", headers=headers)
                status: int | str = response.status_code
            except httpx.HTTPError:
                status = "HTTP_ERROR"
                response = None
            credential_results.append(
                {"credentialSafeId": f"nvidia-key-{index}", "catalogStatus": status}
            )
            if status == 200 and catalog_payload is None and response is not None:
                catalog_payload = response.json()
                selected_headers = headers

        all_credentials_working = all(item["catalogStatus"] == 200 for item in credential_results)
        if catalog_payload is None or selected_headers is None:
            return {
                "configured": True,
                "catalogStatus": credential_results[0]["catalogStatus"],
                "credentialCount": len(keys),
                "allConfiguredCredentialsWorking": False,
                "credentialCatalogResults": credential_results,
                "workingModels": [],
            }
        catalog_models = [
            str(item.get("id", "")) for item in catalog_payload.get("data", []) if item.get("id")
        ]
        configured, duplicates = _configured_models(
            settings.nvidia_lightweight_models,
            settings.resolved_nvidia_standard_models,
        )
        working: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        for tier, model in configured:
            if model not in catalog_models:
                results.append(
                    {
                        "tier": tier,
                        "model": model,
                        "catalogPresent": False,
                        "generationStatus": None,
                    }
                )
                continue
            try:
                probe = await client.post(
                    f"{settings.nvidia_base_url}/chat/completions",
                    headers=selected_headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Reply OK"}],
                        "temperature": 0,
                        "max_tokens": 8,
                    },
                )
                status = probe.status_code
            except httpx.HTTPError:
                status = "HTTP_ERROR"
            results.append(
                {
                    "tier": tier,
                    "model": model,
                    "catalogPresent": True,
                    "generationStatus": status,
                }
            )
            if status == 200:
                working.append({"tier": tier, "model": model})
        return {
            "configured": True,
            "catalogStatus": 200,
            "credentialCount": len(keys),
            "allConfiguredCredentialsWorking": all_credentials_working,
            "credentialCatalogResults": credential_results,
            "catalogModelCount": len(catalog_models),
            "configuredModelCount": len(configured),
            "duplicateConfiguredModels": duplicates,
            "allConfiguredModelsWorking": (not duplicates and len(working) == len(configured)),
            "modelResults": results,
            "workingModels": working,
        }


async def main() -> None:
    settings = Settings()
    google, nvidia = await asyncio.gather(_google(settings), _nvidia(settings))
    print(json.dumps({"GOOGLE": google, "NVIDIA": nvidia}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
