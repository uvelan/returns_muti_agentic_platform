#!/usr/bin/env python3
"""Dependency-free source validation for Stage 4 E2E completion artifacts."""

from __future__ import annotations

import ast
import json
import py_compile
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/evidence/stage4_e2e_completion/source_validation.json"

REQUIRED_FRONTEND_ROUTES = {
    "/customer/returns",
    "/customer/returns/new",
    "/customer/returns/:sessionId",
    "/support/returns",
    "/support/returns/:sessionId",
    "/support/review-queue",
    "/support/operations",
    "/ai-gateway/requests",
    "/ai-gateway/requests/:requestId",
    "/ai-gateway/simulator",
    "/ai-gateway/interceptions",
    "/system/dependencies",
    "/system/dependencies/:dependencyId",
    "/seed-data",
}
REQUIRED_COMPOSE_SERVICES = {
    "sqlserver",
    "sqlserver-init",
    "mongodb",
    "mongodb-rs-init",
    "neo4j",
    "valkey",
    "temporal-postgresql",
    "temporal",
    "temporal-ui",
    "seed-runner",
    "backend",
    "return-workflow-worker",
    "return-orchestrator",
    "outbox-publisher",
    "data-job-worker",
    "frontend",
}
REQUIRED_BACKEND_ROUTE_FRAGMENTS = {
    'prefix="/api/v1/returns"',
    'prefix="/api/v1/support"',
    'prefix="/api/v1/ai-gateway"',
    'prefix="/api/v1/seed-data"',
    'prefix="/api/v1/system/dependencies"',
    '@router.get("/{session_id}/stream")',
    '@router.post("/jobs/{job_id}/cancel"',
    '@router.post("/jobs/{job_id}/retry"',
}
FORBIDDEN_PRODUCTION_FRAGMENTS = {
    "resources.mongodb": "obsolete Mongo resource field",
    "settings.mongodb_database": "obsolete Mongo settings field",
    "storage.return-platform.local": "invented export URL",
    '"orchestrationState": {"$in": ["QUEUED", "RUNNING", "WAITING_INTERCEPTION"': "paused-session hot loop",
}
REQUIRED_AI_PROVIDERS = {"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"}


def add(checks: list[dict[str, Any]], name: str, passed: bool, evidence: Any) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def compile_python(checks: list[dict[str, Any]]) -> None:
    failures: list[str] = []
    for base in (ROOT / "backend/src", ROOT / "backend/scripts", ROOT / "backend/tests"):
        for path in base.rglob("*.py"):
            try:
                py_compile.compile(str(path), doraise=True)
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except Exception as error:  # pragma: no cover - validation command
                failures.append(f"{path.relative_to(ROOT)}: {error}")
    add(checks, "python_source_compiles", not failures, failures or "All Python source files compiled and parsed.")


def validate_frontend_routes(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "frontend/src/routes.ts"
    text = path.read_text(encoding="utf-8")
    routes = set(re.findall(r'path:\s*"([^"]+)"', text))
    missing = sorted(REQUIRED_FRONTEND_ROUTES - routes)
    capability_count = len(re.findall(r'capability:\s*"LIVE"', text))
    add(checks, "required_frontend_routes", not missing, {"missing": missing, "routeCount": len(routes)})
    add(
        checks,
        "frontend_routes_live_only",
        'capability: "FIXTURE"' not in text and 'capability: "BLOCKED"' not in text,
        {"liveCapabilityCount": capability_count},
    )


def validate_backend_routes(checks: list[dict[str, Any]]) -> None:
    files = list((ROOT / "backend/src/return_platform").rglob("*.py"))
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in files)
    missing = sorted(fragment for fragment in REQUIRED_BACKEND_ROUTE_FRAGMENTS if fragment not in corpus)
    add(checks, "required_backend_routes", not missing, {"missingFragments": missing})


def validate_compose(checks: list[dict[str, Any]]) -> None:
    data = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = set(data.get("services", {}))
    missing = sorted(REQUIRED_COMPOSE_SERVICES - services)
    add(checks, "runtime_topology", not missing, {"services": sorted(services), "missing": missing})


def validate_seed(checks: list[dict[str, Any]]) -> None:
    namespace: dict[str, Any] = {}
    path = ROOT / "backend/src/return_platform/operations/seed_manifest.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    scenarios = namespace["SEED_SCENARIOS"]
    counts: dict[str, int] = {}
    for scenario in scenarios:
        decision = str(scenario["expectedDecision"])
        counts[decision] = counts.get(decision, 0) + 1
    positive = counts.get("APPROVE", 0)
    negative = counts.get("REJECT", 0) + counts.get("REVIEW_REQUIRED", 0)
    add(
        checks,
        "seed_scenario_matrix",
        positive >= 5 and negative >= 5,
        {"counts": counts, "positive": positive, "negativeOrReview": negative},
    )


def validate_ai(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "backend/src/return_platform/ai_gateway/providers.py"
    text = path.read_text(encoding="utf-8")
    discovered = {provider for provider in REQUIRED_AI_PROVIDERS if f'"{provider}"' in text}
    add(checks, "ai_provider_registry", discovered == REQUIRED_AI_PROVIDERS, {"providers": sorted(discovered)})
    service = (ROOT / "backend/src/return_platform/ai_gateway/service.py").read_text(encoding="utf-8")
    policy_fragments = ["_SENSITIVE_KEY_FRAGMENTS", "ai_global_timeout_seconds", "consume_ai_quota", "INTERCEPTION_PENDING"]
    missing = [fragment for fragment in policy_fragments if fragment not in service]
    add(checks, "ai_gateway_policy_controls", not missing, {"missing": missing})


def validate_forbidden(checks: list[dict[str, Any]]) -> None:
    production_roots = [ROOT / "backend/src", ROOT / "backend/scripts", ROOT / "frontend/src"]
    violations: list[dict[str, str]] = []
    for base in production_roots:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for fragment, reason in FORBIDDEN_PRODUCTION_FRAGMENTS.items():
                if fragment in text:
                    violations.append({"path": str(path.relative_to(ROOT)), "fragment": fragment, "reason": reason})
    add(checks, "forbidden_runtime_patterns_absent", not violations, violations or "No forbidden patterns found.")


def validate_placeholders(checks: list[dict[str, Any]]) -> None:
    forbidden = ("Preview data for", "placeholder page", "not implemented yet", "coming soon")
    violations: list[str] = []
    for path in (ROOT / "frontend/src/features").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            if phrase.lower() in text:
                violations.append(f"{path.relative_to(ROOT)}: {phrase}")
    add(checks, "explicit_placeholder_pages_absent", not violations, violations or "No explicit placeholder pages found.")


def validate_integrity_guards(checks: list[dict[str, Any]]) -> None:
    repository = (ROOT / "backend/src/return_platform/operations/repository.py").read_text(encoding="utf-8")
    jobs = (ROOT / "backend/src/return_platform/data_console/api/jobs.py").read_text(encoding="utf-8")
    scenarios = (ROOT / "backend/src/return_platform/data_console/api/scenarios.py").read_text(encoding="utf-8")
    main = (ROOT / "backend/src/return_platform/main.py").read_text(encoding="utf-8")
    required = {
        "paused_sessions_not_claimed": '"orchestrationState": {"$in": ["QUEUED", "RUNNING"]}' in repository,
        "support_transaction": "with_transaction(transaction)" in repository,
        "job_transaction": "with_transaction(transaction)" in jobs,
        "job_cancel_retry": "async def cancel(" in jobs and "async def retry(" in jobs,
        "scenario_digest_approval": "validatedDigest" in scenarios and "generatedDigest" in scenarios,
        "cors_mutation_methods": 'allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]' in main,
    }
    add(checks, "integrity_and_concurrency_guards", all(required.values()), required)


def main() -> int:
    checks: list[dict[str, Any]] = []
    compile_python(checks)
    validate_frontend_routes(checks)
    validate_backend_routes(checks)
    validate_compose(checks)
    validate_seed(checks)
    validate_ai(checks)
    validate_forbidden(checks)
    validate_placeholders(checks)
    validate_integrity_guards(checks)
    failed = [check for check in checks if check["status"] != "PASS"]
    payload = {
        "stage": "Stage 4 — E2E Completion",
        "validationLevel": "SOURCE_VALIDATED",
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "dockerAvailable": False,
        },
        "command": "python3 scripts/validate_stage4_source.py",
        "generatedAt": datetime.now(UTC).isoformat(),
        "exitCode": 1 if failed else 0,
        "status": "FAILED" if failed else "PASSED",
        "checks": checks,
        "limitations": [
            "This gate does not replace Ruff, strict mypy, pytest, frontend dependency-backed builds, Docker Compose startup, or live provider validation.",
            "Those gates require dependencies and runtime infrastructure unavailable in the audit host.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
