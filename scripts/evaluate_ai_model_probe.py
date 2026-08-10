#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final

REQUIRED_PROVIDERS: Final = ("GOOGLE", "NVIDIA")
REQUIRED_TIERS: Final = ("LIGHTWEIGHT", "STANDARD")


def normalize_status(value: Any) -> int | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
        return value or None
    return None


def evaluate_provider(name: str, raw: Any) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    working: list[dict[str, str]] = []
    rate_limited: list[dict[str, str]] = []

    if not isinstance(raw, dict):
        return {
            "status": "FAILED",
            "accepted": False,
            "workingModels": [],
            "rateLimitedModels": [],
            "hardFailures": [f"{name}: provider evidence missing"],
            "warnings": [],
        }

    if raw.get("configured") is not True:
        failures.append(f"{name}: provider is not configured")
    if normalize_status(raw.get("catalogStatus")) != 200:
        failures.append(f"{name}: catalogStatus={raw.get('catalogStatus')!r}")
    if raw.get("allConfiguredCredentialsWorking") is not True:
        failures.append(f"{name}: configured credential validation failed")

    model_results = raw.get("modelResults")
    configured_count = raw.get("configuredModelCount")

    if not isinstance(model_results, list):
        failures.append(f"{name}: modelResults is invalid")
        model_results = []

    if not isinstance(configured_count, int) or configured_count <= 0:
        failures.append(f"{name}: configuredModelCount is invalid")
    elif configured_count != len(model_results):
        failures.append(
            f"{name}: configuredModelCount={configured_count}, "
            f"modelResults={len(model_results)}"
        )

    duplicates = raw.get("duplicateConfiguredModels", [])
    if isinstance(duplicates, list) and duplicates:
        failures.append(f"{name}: duplicate models={duplicates}")

    for index, item in enumerate(model_results):
        if not isinstance(item, dict):
            failures.append(f"{name}: model result {index} is invalid")
            continue

        model = str(item.get("model") or f"<model-{index}>")
        tier = str(item.get("tier") or "UNKNOWN").upper()
        status = normalize_status(item.get("generationStatus"))

        if item.get("catalogPresent") is not True:
            failures.append(f"{name}: model absent from catalog: {model}")
        if tier not in REQUIRED_TIERS:
            failures.append(f"{name}: invalid tier {tier} for {model}")

        if status == 200:
            working.append({"model": model, "tier": tier})
        elif status == 429:
            rate_limited.append({"model": model, "tier": tier})
            warnings.append(f"{name}: {model} is rate-limited")
        else:
            failures.append(
                f"{name}: {model} generationStatus={item.get('generationStatus')!r}"
            )

    if failures:
        provider_status = "FAILED"
    elif rate_limited:
        provider_status = "DEGRADED_RATE_LIMITED"
    else:
        provider_status = "HEALTHY"

    return {
        "status": provider_status,
        "accepted": not failures,
        "workingModels": working,
        "rateLimitedModels": rate_limited,
        "hardFailures": failures,
        "warnings": warnings,
    }


def evaluate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "accepted": False,
            "overallStatus": "FAIL",
            "providerResults": {},
            "tierCoverage": {},
            "hardFailures": ["Probe evidence must be a JSON object"],
            "warnings": [],
        }

    providers = {
        name: evaluate_provider(name, payload.get(name)) for name in REQUIRED_PROVIDERS
    }

    failures = [
        value for result in providers.values() for value in result["hardFailures"]
    ]
    warnings = [value for result in providers.values() for value in result["warnings"]]

    tier_coverage: dict[str, list[dict[str, str]]] = {
        tier: [] for tier in REQUIRED_TIERS
    }

    for provider_name, result in providers.items():
        for item in result["workingModels"]:
            tier = item["tier"]
            if tier in tier_coverage:
                tier_coverage[tier].append(
                    {"provider": provider_name, "model": item["model"]}
                )

    for tier, routes in tier_coverage.items():
        if not routes:
            failures.append(f"No live HTTP 200 route for tier {tier}")

    accepted = not failures
    if not accepted:
        overall = "FAIL"
    elif any(
        result["status"] == "DEGRADED_RATE_LIMITED" for result in providers.values()
    ):
        overall = "PASS_WITH_WARNING"
    else:
        overall = "PASS"

    return {
        "accepted": accepted,
        "overallStatus": overall,
        "providerResults": providers,
        "tierCoverage": tier_coverage,
        "hardFailures": failures,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe_json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        payload = json.loads(args.probe_json.read_text(encoding="utf-8"))
        result = evaluate(payload)
    except (OSError, json.JSONDecodeError) as exc:
        result = {
            "accepted": False,
            "overallStatus": "FAIL",
            "providerResults": {},
            "tierCoverage": {},
            "hardFailures": [f"Unable to read probe evidence: {exc}"],
            "warnings": [],
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
