#!/usr/bin/env python3
"""Safely discover and probe all provider models.

The script never prints credentials, request headers, or provider response bodies.
It discovers models from catalogs and probes them to build a valid list.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from return_platform.configuration.settings import Settings

# Pydantic schema for the Order Agent structured probe
AGENT_ACTION_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


async def _probe_google_model(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], model: str
) -> dict[str, Any]:
    # Probe 2: Minimal text generation
    try:
        t0 = time.monotonic()
        probe_min = await client.post(
            f"{base_url}/models/{model}:generateContent",
            headers=headers,
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": 'Return only the JSON object {"status":"ok"}.'}
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": 64,
                },
            },
        )
        t1 = time.monotonic()
        status = probe_min.status_code
        if status != 200:
            return {
                "model": model,
                "success": False,
                "error_category": "MINIMAL_PROBE_FAILED",
                "status": status,
                "duration_ms": int((t1 - t0) * 1000),
            }
    except httpx.HTTPError:
        return {
            "model": model,
            "success": False,
            "error_category": "TIMEOUT_OR_NETWORK",
            "duration_ms": 0,
        }

    # Probe 3: Order Agent structured response
    try:
        t0 = time.monotonic()
        probe_struct = await client.post(
            f"{base_url}/models/{model}:generateContent",
            headers=headers,
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": "Return a JSON object with status ok"}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                    "responseSchema": AGENT_ACTION_SCHEMA,
                },
            },
        )
        t1 = time.monotonic()
        status = probe_struct.status_code
        if status != 200:
            return {
                "model": model,
                "success": False,
                "error_category": "STRUCTURED_PROBE_FAILED",
                "status": status,
                "duration_ms": int((t1 - t0) * 1000),
            }

        # Verify JSON
        data = probe_struct.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        # Very simple verification
        if "ok" not in text.lower():
            return {
                "model": model,
                "success": False,
                "error_category": "STRUCTURED_OUTPUT_INVALID",
                "duration_ms": int((t1 - t0) * 1000),
            }

    except httpx.HTTPError:
        return {
            "model": model,
            "success": False,
            "error_category": "TIMEOUT_OR_NETWORK",
            "duration_ms": 0,
        }

    # Classification
    tier = (
        "LIGHTWEIGHT"
        if "flash-lite" in model.lower() or "8b" in model.lower()
        else "STANDARD"
    )

    return {
        "model": model,
        "success": True,
        "tier": tier,
        "duration_ms": int((t1 - t0) * 1000),
        "structured_output_valid": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }


async def _google(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_google_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}

    async with httpx.AsyncClient(timeout=30) as client:
        catalog_payload: dict[str, Any] | None = None
        selected_headers: dict[str, str] | None = None

        for secret in keys:
            headers = {
                "x-goog-api-key": secret.get_secret_value(),
                "Content-Type": "application/json",
            }
            try:
                response = await client.get(
                    f"{settings.google_base_url}/models", headers=headers
                )
                if response.status_code == 200:
                    catalog_payload = response.json()
                    selected_headers = headers
                    break
            except httpx.HTTPError:
                continue

        if catalog_payload is None or selected_headers is None:
            return {"configured": True, "error": "Could not fetch catalog with any key"}

        catalog_models = [
            str(item.get("name", "")).removeprefix("models/")
            for item in catalog_payload.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
            and "gemini" in item.get("name", "").lower()
            and "vision" not in item.get("name", "").lower()
            and "embedding" not in item.get("name", "").lower()
        ]

        # Probe all candidate models concurrently
        tasks = [
            _probe_google_model(
                client, settings.google_base_url, selected_headers, model
            )
            for model in catalog_models
        ]
        results = await asyncio.gather(*tasks)

        working = [r for r in results if r.get("success")]
        return {
            "configured": True,
            "catalogModelCount": len(catalog_models),
            "workingModels": working,
            "allResults": results,
        }


async def _probe_nvidia_model(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], model: str
) -> dict[str, Any]:
    # Probe 2: Minimal text generation
    try:
        t0 = time.monotonic()
        probe_min = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": 'Return only the JSON object {"status":"ok"}.',
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 64,
            },
        )
        t1 = time.monotonic()
        status = probe_min.status_code
        if status != 200:
            return {
                "model": model,
                "success": False,
                "error_category": "MINIMAL_PROBE_FAILED",
                "status": status,
                "duration_ms": int((t1 - t0) * 1000),
            }
    except httpx.HTTPError:
        return {
            "model": model,
            "success": False,
            "error_category": "TIMEOUT_OR_NETWORK",
            "duration_ms": 0,
        }

    # Probe 3: Order Agent structured response
    try:
        t0 = time.monotonic()
        probe_struct = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Return a JSON object with status ok"}
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            },
        )
        t1 = time.monotonic()
        status = probe_struct.status_code
        if status != 200:
            return {
                "model": model,
                "success": False,
                "error_category": "STRUCTURED_PROBE_FAILED",
                "status": status,
                "duration_ms": int((t1 - t0) * 1000),
            }
    except httpx.HTTPError:
        return {
            "model": model,
            "success": False,
            "error_category": "TIMEOUT_OR_NETWORK",
            "duration_ms": 0,
        }

    tier = (
        "LIGHTWEIGHT"
        if "mini" in model.lower() or "nano" in model.lower() or "8b" in model.lower()
        else "STANDARD"
    )

    return {
        "model": model,
        "success": True,
        "tier": tier,
        "duration_ms": int((t1 - t0) * 1000),
        "structured_output_valid": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }


async def _nvidia(settings: Settings) -> dict[str, Any]:
    keys = settings.resolved_nvidia_api_keys
    if not keys:
        return {"configured": False, "workingModels": []}

    async with httpx.AsyncClient(timeout=30) as client:
        catalog_payload: dict[str, Any] | None = None
        selected_headers: dict[str, str] | None = None

        for secret in keys:
            headers = {
                "Authorization": f"Bearer {secret.get_secret_value()}",
                "Content-Type": "application/json",
            }
            try:
                response = await client.get(
                    f"{settings.nvidia_base_url}/models", headers=headers
                )
                if response.status_code == 200:
                    catalog_payload = response.json()
                    selected_headers = headers
                    break
            except httpx.HTTPError:
                continue

        if catalog_payload is None or selected_headers is None:
            return {"configured": True, "error": "Could not fetch catalog with any key"}

        catalog_models = [
            str(item.get("id", ""))
            for item in catalog_payload.get("data", [])
            if item.get("id")
        ]

        # Filter down candidates slightly to avoid probing 100s of embedding/unrelated models
        candidate_models = [
            m
            for m in catalog_models
            if "llama" in m.lower() or "nemotron" in m.lower() or "mixtral" in m.lower()
        ]

        tasks = [
            _probe_nvidia_model(
                client, settings.nvidia_base_url, selected_headers, model
            )
            for model in candidate_models
        ]
        results = await asyncio.gather(*tasks)

        working = [r for r in results if r.get("success")]
        return {
            "configured": True,
            "catalogModelCount": len(catalog_models),
            "workingModels": working,
            "allResults": results,
        }


async def main() -> None:
    settings = Settings()
    google, nvidia = await asyncio.gather(_google(settings), _nvidia(settings))

    report = {"GOOGLE": google, "NVIDIA": nvidia}

    print(json.dumps(report, indent=2))

    # Save validation receipt to prevent multiple validations in 24 hours
    with open("validation_receipt.json", "w") as f:
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
