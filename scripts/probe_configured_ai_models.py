#!/usr/bin/env python3
"""Safely discover and minimally probe configured Gemini and NVIDIA models.

The script never prints credentials, request headers, or provider response bodies.
It is intended for operator use before updating the model pools in `.env.example`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from typing import Any

import httpx

from return_platform.configuration.settings import Settings


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


async def _google(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_google_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}
    key = keys[0].get_secret_value()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{settings.google_base_url}/models", headers=headers
        )
        if response.status_code != 200:
            return {
                "configured": True,
                "catalogStatus": response.status_code,
                "workingModels": [],
            }
        models = [
            str(item.get("name", "")).removeprefix("models/")
            for item in response.json().get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        ]
        lightweight = _unique(
            [
                *settings.google_lightweight_models,
                "gemini-2.5-flash-lite",
                *(
                    model
                    for model in models
                    if "flash-lite" in model and "preview" not in model
                ),
            ]
        )
        standard = _unique(
            [
                *settings.resolved_google_standard_models,
                "gemini-2.5-flash",
                *(
                    model
                    for model in models
                    if "flash" in model
                    and "lite" not in model
                    and "preview" not in model
                ),
            ]
        )
        working: list[dict[str, str]] = []
        selected: set[str] = set()
        for tier, candidates, target in (
            ("LIGHTWEIGHT", lightweight, 2),
            ("STANDARD", standard, 2),
        ):
            for model in candidates:
                if model not in models or model in selected:
                    continue
                try:
                    probe = await client.post(
                        f"{settings.google_base_url}/models/{model}:generateContent",
                        headers=headers,
                        json={
                            "contents": [
                                {"role": "user", "parts": [{"text": "Reply OK"}]}
                            ],
                            "generationConfig": {
                                "temperature": 0,
                                "maxOutputTokens": 8,
                            },
                        },
                    )
                except httpx.HTTPError:
                    continue
                if probe.status_code == 200:
                    working.append({"tier": tier, "model": model})
                    selected.add(model)
                    if sum(item["tier"] == tier for item in working) >= target:
                        break
        return {
            "configured": True,
            "catalogStatus": 200,
            "catalogModelCount": len(models),
            "workingModels": working,
        }


async def _nvidia(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_nvidia_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}
    key = keys[0].get_secret_value()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.nvidia_base_url}/models", headers=headers
        )
        if response.status_code != 200:
            return {
                "configured": True,
                "catalogStatus": response.status_code,
                "workingModels": [],
            }
        models = [
            str(item.get("id", ""))
            for item in response.json().get("data", [])
            if item.get("id")
        ]
        lightweight = _unique(
            [
                "meta/llama-3.2-3b-instruct",
                *(
                    model
                    for model in models
                    if re.search(r"(?:^|[-/])(?:3b|7b|8b)(?:[-/]|$)", model.lower())
                    and not any(
                        token in model.lower()
                        for token in ("guard", "safety", "embed", "rerank")
                    )
                ),
            ]
        )
        standard = _unique(
            [
                *settings.resolved_nvidia_standard_models,
                *(
                    model
                    for model in models
                    if any(
                        token in model.lower()
                        for token in ("nemotron", "12b", "22b", "30b", "70b")
                    )
                ),
                *models,
            ]
        )
        working: list[dict[str, str]] = []
        selected: set[str] = set()
        for tier, candidates, target in (
            ("LIGHTWEIGHT", lightweight, 3),
            ("STANDARD", standard, 2),
        ):
            attempts = 0
            for model in candidates:
                if model not in models or model in selected:
                    continue
                attempts += 1
                if attempts > 30:
                    break
                try:
                    probe = await client.post(
                        f"{settings.nvidia_base_url}/chat/completions",
                        headers=headers,
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "Reply OK"}],
                            "temperature": 0,
                            "max_tokens": 8,
                        },
                    )
                except httpx.HTTPError:
                    continue
                if probe.status_code == 200:
                    working.append({"tier": tier, "model": model})
                    selected.add(model)
                    if sum(item["tier"] == tier for item in working) >= target:
                        break
        return {
            "configured": True,
            "catalogStatus": 200,
            "catalogModelCount": len(models),
            "workingModels": working,
        }


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    google, nvidia = await asyncio.gather(_google(settings), _nvidia(settings))
    print(json.dumps({"GOOGLE": google, "NVIDIA": nvidia}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
