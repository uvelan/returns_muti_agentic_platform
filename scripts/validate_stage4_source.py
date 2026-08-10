#!/usr/bin/env python3
"""Dependency-light source validation for the Return Platform completion handoff."""

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

#: The four canonical domains. This listed fifteen legacy paths from
#: `frontend/src/routes.ts` until Wave F4 deleted that file along with the
#: other 61 routes.
REQUIRED_FRONTEND_ROUTES = {
    "/returns",
    "/config",
    "/graph-schema",
    "/ai",
}
#: Wave G1 split these into three profiles. `seed-runner` and `temporal-ui` are
#: `dev-tools` now, and `runtime-configuration-init` moved to the default
#: profile because it is bootstrap, not an application service.
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
APP_PROFILE_SERVICES = {
    "backend",
    "return-workflow-worker",
    "return-orchestrator",
    "outbox-publisher",
    "data-job-worker",
    "frontend",
}
REQUIRED_MONGO_COLLECTIONS = {
    "salesInv",
    "customerOutboundCDM",
    "shipmentInfo",
    "lkpSearchProduct",
    "customers",
    "products",
    "orders",
    "operational_returns",
    "operational_events",
    "support_cases",
    "ai_gateway_traces",
    "ai_gateway_settings",
    "ai_gateway_rate_limits",
    "worker_heartbeats",
    "seed_metadata",
    "return_sessions",
    "return_session_audit_events",
    "return_session_outbox_events",
    "return_session_agent_decisions",
    "workspaces",
    "sandbox_records",
    "jobs",
    "job_commands",
    "job_artifacts",
    "scenarios",
    "scenario_records",
    "audit",
    "graph_evidence_runs",
    "associate_conversations",
    "discovery_locks",
    "ai_studio_proposals",
    "graph_sync_runs",
    "feedback_learning_records",
}

#: One file per provider, plus the shared pieces. `factory.py` became
#: `registry.py` in the same `ai_gateway/ -> ai/` migration that moved this
#: whole directory; the check asked for the old name and reported it missing.
REQUIRED_PROVIDER_FILES = {
    "google.py",
    "nvidia.py",
    "openai.py",
    "anthropic.py",
    "ollama.py",
    "simulator.py",
    "manual.py",
    "registry.py",
    "contracts.py",
    "http.py",
}
REQUIRED_HOST_SCRIPTS = {
    "bootstrap_host.sh",
    "run_backend_host.sh",
    "run_frontend_host.sh",
    "run_worker_host.sh",
    "run_all_host.sh",
    "infra.sh",
    "bootstrap_host.ps1",
    "run_backend_host.ps1",
    "run_frontend_host.ps1",
    "run_worker_host.ps1",
}
FORBIDDEN_RUNTIME_FRAGMENTS = {
    "resources.mongodb": "obsolete Mongo resource field",
    "settings.mongodb_database": "obsolete Mongo settings field",
    "storage.return-platform.local": "invented export URL",
    (
        '"orchestrationState": {"$in": ["QUEUED", "RUNNING", "WAITING_INTERCEPTION"'
    ): "paused-session hot loop",
    "RMA-{session.id": "orchestrator-generated authoritative RMA",
    "BAY-A1": "hard-coded bay reference",
    'WH-CHENNAI-01"\n        bay_reference': "hard-coded warehouse/bay pair",
}


def add(checks: list[dict[str, Any]], name: str, passed: bool, evidence: Any) -> None:
    checks.append(
        {"name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence}
    )


def frozenset_assignment(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call) or not node.value.args:
            break
        if (
            not isinstance(node.value.func, ast.Name)
            or node.value.func.id != "frozenset"
        ):
            break
        literal = ast.literal_eval(node.value.args[0])
        return frozenset(str(item) for item in literal)
    raise ValueError(f"Unable to read frozenset assignment {name} from {path}")


def compile_python(checks: list[dict[str, Any]]) -> None:
    failures: list[str] = []
    roots = (
        ROOT / "backend/src",
        ROOT / "backend/scripts",
        ROOT / "backend/tests",
        ROOT / "scripts",
    )
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                py_compile.compile(str(path), doraise=True)
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except Exception as error:
                failures.append(f"{path.relative_to(ROOT)}: {error}")
    add(
        checks,
        "python_source_compiles",
        not failures,
        failures or "All Python source parsed and compiled.",
    )


def validate_frontend(checks: list[dict[str, Any]]) -> None:
    """The four-domain shell, after Wave F4.

    Rewritten rather than deleted. It used to assert fifteen legacy routes in
    `frontend/src/routes.ts` and three Associate-page components, all of which
    F4 removed -- so this raised `FileNotFoundError` and took
    `05_run_contract_and_config_checks.sh` down with it. What is worth checking
    now is the property F4 established: four domains, declared in one registry,
    with no legacy shell left behind.
    """
    registry_text = (ROOT / "frontend/src/domains/registry.ts").read_text(
        encoding="utf-8"
    )
    routes = set(re.findall(r'path:\s*"([^"]+)"', registry_text))
    add(
        checks,
        "required_frontend_routes",
        routes == REQUIRED_FRONTEND_ROUTES,
        {
            "missing": sorted(REQUIRED_FRONTEND_ROUTES - routes),
            "unexpected": sorted(routes - REQUIRED_FRONTEND_ROUTES),
            "routes": sorted(routes),
        },
    )
    # Equality, not containment: F4's end state is "exactly four user routes",
    # so a fifth appearing is as much a regression as one going missing.
    legacy_paths = {
        "frontend/src/routes.ts": (ROOT / "frontend/src/routes.ts").exists(),
        "frontend/src/features": (ROOT / "frontend/src/features").exists(),
    }
    add(
        checks,
        "legacy_frontend_is_gone",
        not any(legacy_paths.values()),
        legacy_paths,
    )
    app_text = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    add(
        checks,
        "shell_has_no_legacy_branch",
        "VersionOneApp" not in app_text and 'base="/v1"' not in app_text,
        {"checked": "frontend/src/App.tsx"},
    )


def validate_provider_layout(checks: list[dict[str, Any]]) -> None:
    # Moved in 6ff5162 ("Phase 13 ai_gateway -> canonical ai/ migration"). The
    # old directory still exists but holds only __init__.py, so this reported
    # every provider missing rather than failing outright.
    provider_dir = ROOT / "backend/src/return_platform/ai/providers"
    present = {path.name for path in provider_dir.glob("*.py")}
    missing = sorted(REQUIRED_PROVIDER_FILES - present)
    monolith_absent = not (
        ROOT / "backend/src/return_platform/ai/providers.py"
    ).exists()
    provider_source = "\n".join(
        path.read_text(encoding="utf-8") for path in provider_dir.glob("*.py")
    )
    providers = {
        name
        for name in ("GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR")
        if f'"{name}"' in provider_source
    }
    add(
        checks,
        "provider_files_are_isolated",
        not missing and monolith_absent,
        {
            "missing": missing,
            "monolithAbsent": monolith_absent,
            "files": sorted(present),
        },
    )
    add(
        checks,
        "provider_registry_complete",
        len(providers) == 6,
        {"providers": sorted(providers)},
    )


def validate_schema_and_data_console(checks: list[dict[str, Any]]) -> None:
    registry_path = ROOT / "backend/config/schema_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assets = registry.get("assets", [])
    graph = registry.get("graph", {})
    mongo_assets = [item for item in assets if item.get("engine") == "MONGODB"]
    sql_assets = [item for item in assets if item.get("engine") == "SQLSERVER"]
    mongo_names = {str(item.get("name")) for item in mongo_assets}
    missing_mongo = sorted(REQUIRED_MONGO_COLLECTIONS - mongo_names)
    add(
        checks,
        "physical_schema_registry",
        not missing_mongo and len(sql_assets) >= 8,
        {
            "mongoAssets": len(mongo_assets),
            "sqlAssets": len(sql_assets),
            "totalAssets": len(assets),
            "missingMongoCollections": missing_mongo,
        },
    )
    add(
        checks,
        "graph_schema_registry",
        len(graph.get("nodes", [])) >= 10 and len(graph.get("relationships", [])) >= 10,
        {
            "nodes": len(graph.get("nodes", [])),
            "relationships": len(graph.get("relationships", [])),
        },
    )
    ai_studio_path = ROOT / "backend/src/return_platform/data_platform/ai_studio.py"
    declared_generators = {
        str(field.get("generator"))
        for asset in assets
        for field in asset.get("fields", [])
    }
    supported_generators = frozenset_assignment(ai_studio_path, "SUPPORTED_GENERATORS")
    direct_mongo = frozenset_assignment(ai_studio_path, "DIRECT_MONGO_COLLECTIONS")
    direct_sql = frozenset_assignment(ai_studio_path, "DIRECT_SQL_ASSETS")
    writable_mongo = {
        str(asset.get("name"))
        for asset in mongo_assets
        if asset.get("writable_in_sandbox") is True
    }
    writable_sql = {
        str(asset.get("asset_id"))
        for asset in sql_assets
        if asset.get("writable_in_sandbox") is True
    }
    add(
        checks,
        "ai_studio_generator_coverage",
        declared_generators == supported_generators,
        {
            "declared": len(declared_generators),
            "supported": len(supported_generators),
            "missingImplementations": sorted(
                declared_generators - supported_generators
            ),
            "unusedImplementations": sorted(supported_generators - declared_generators),
        },
    )
    add(
        checks,
        "ai_studio_direct_write_boundaries",
        direct_mongo == writable_mongo and direct_sql == writable_sql,
        {
            "directMongo": sorted(direct_mongo),
            "writableMongo": sorted(writable_mongo),
            "directSql": sorted(direct_sql),
            "writableSql": sorted(writable_sql),
        },
    )
    # The four `data_console/api/*` routers this used to require were deleted in
    # Wave F5 at zero consumers, and the package with them. What is left is the
    # `data_platform` service layer they called, which is still real and still
    # worth asserting.
    required_files = [
        ROOT / "backend/src/return_platform/data_platform/ai_studio.py",
        ROOT / "backend/src/return_platform/data_platform/graph/schema.py",
        ROOT / "backend/src/return_platform/data_platform/graph/sync_service.py",
    ]
    add(
        checks,
        "data_platform_services_present",
        all(path.exists() for path in required_files),
        [str(path.relative_to(ROOT)) for path in required_files],
    )


def validate_domain_flow(checks: list[dict[str, Any]]) -> None:
    associate = (
        ROOT / "backend/src/return_platform/operations/associate_flow.py"
    ).read_text(encoding="utf-8")
    orchestrator = (
        ROOT / "backend/src/return_platform/operations/orchestrator.py"
    ).read_text(encoding="utf-8")
    sql = (
        ROOT / "backend/src/return_platform/operations/sql_business_state.py"
    ).read_text(encoding="utf-8")
    feedback = (
        ROOT / "backend/src/return_platform/operations/feedback_service.py"
    ).read_text(encoding="utf-8")
    support_files = list(
        (ROOT / "backend/src/return_platform/operations/return_support/providers").glob(
            "*.py"
        )
    )
    support = "\n".join(path.read_text(encoding="utf-8") for path in support_files)
    requirements = {
        "graph_first_discovery": (
            "_graph_candidates" in associate and "_targeted_graph_upsert" in associate
        ),
        "digest_bound_lock": "lockDigest" in associate
        and "partialFilterExpression" in associate,
        "typed_return_details": all(
            field in associate
            for field in (
                "returnQuantity",
                "packageCount",
                "shippingPathExpectation",
            )
        ),
        "support_ticket_provider": (
            "ReturnSupportProvider" in support and "Idempotency-Key" in support
        ),
        "authoritative_sql": "persist_support_result" in sql and "assign_bay" in sql,
        "capacity_aware_bay": "assigned_packages" in sql
        and "supported_product_types" in sql,
        "feedback_review_queue": (
            "REVIEW_PENDING" in feedback
            and 'self._records.replace_one({"sessionId": session.id}' in feedback
        ),
        "lock_release": "release_discovery_lock" in orchestrator,
    }
    add(
        checks, "hld_aligned_operational_flow", all(requirements.values()), requirements
    )


def validate_seed(checks: list[dict[str, Any]]) -> None:
    namespace: dict[str, Any] = {}
    path = ROOT / "backend/src/return_platform/operations/seed_manifest.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    scenarios = namespace["SEED_SCENARIOS"]
    counts: dict[str, int] = {}
    for scenario in scenarios:
        decision = str(scenario["expectedDecision"])
        counts[decision] = counts.get(decision, 0) + 1
    add(
        checks,
        "seed_scenario_matrix",
        counts.get("APPROVE", 0) >= 5
        and counts.get("REJECT", 0) + counts.get("REVIEW_REQUIRED", 0) >= 5,
        counts,
    )
    seed_text = path.read_text(encoding="utf-8")
    collections = (
        "salesInv",
        "customerOutboundCDM",
        "shipmentInfo",
        "lkpSearchProduct",
    )
    add(
        checks,
        "hld_source_collections_seeded",
        all(name in seed_text for name in collections),
        {"collections": collections},
    )
    coordinator = (
        ROOT / "backend/src/return_platform/operations/seed_coordinator.py"
    ).read_text(encoding="utf-8")
    legacy_fragments = ("MERGE (order:Order", "[:PLACED]", "[:CONTAINS]")
    add(
        checks,
        "seed_uses_canonical_graph_sync",
        "GraphSyncService" in coordinator
        and "GraphSyncScope.SOURCE_MONGODB" in coordinator
        and not any(fragment in coordinator for fragment in legacy_fragments),
        {
            "canonicalService": "GraphSyncService",
            "scope": "SOURCE_MONGODB",
            "forbiddenLegacyFragments": legacy_fragments,
        },
    )


def validate_runtime_boundaries(checks: list[dict[str, Any]]) -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose.get("services", {})
    missing = sorted(REQUIRED_COMPOSE_SERVICES - set(services))
    app_profile_errors = sorted(
        service
        for service in APP_PROFILE_SERVICES
        if "containerized-app" not in services.get(service, {}).get("profiles", [])
    )
    add(
        checks,
        "runtime_topology",
        not missing,
        {"missing": missing, "services": sorted(services)},
    )
    add(
        checks,
        "host_app_not_docker_required",
        not app_profile_errors,
        {"profileErrors": app_profile_errors},
    )
    missing_scripts = sorted(
        name for name in REQUIRED_HOST_SCRIPTS if not (ROOT / "scripts" / name).exists()
    )
    add(checks, "host_run_scripts", not missing_scripts, {"missing": missing_scripts})


def validate_migrations_and_readme(checks: list[dict[str, Any]]) -> None:
    # Moved out of `infra/sqlserver/init/` in b19570f, when Phase 4 gave the
    # migrations a runner inside the package. This path was never updated, so
    # the script has raised FileNotFoundError -- and taken three gate scripts
    # with it -- since that commit, independently of Wave F.
    migration = (
        ROOT
        / "backend/src/return_platform/configuration/sql_migrations/002_domain_models.sql"
    ).read_text(encoding="utf-8")
    tables = (
        "dbo.return_items",
        "dbo.return_tracking",
        "integration.return_support_ticket",
        "platform.bay_configuration",
        "platform.bay_assignment",
        "platform.feedback_recommendation",
    )
    add(
        checks,
        "domain_sql_migration",
        all(table in migration for table in tables),
        {"tables": tables},
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = (
        "./scripts/bootstrap_host.sh",
        "./scripts/infra.sh start",
        "./scripts/run_all_host.sh",
        "npm run contracts:check",
        "python3.13 scripts/validate_stage4_contracts.py",
    )
    add(
        checks,
        "readme_runbook_commands",
        all(command in readme for command in commands),
        {"commands": commands},
    )


def validate_forbidden(checks: list[dict[str, Any]]) -> None:
    violations: list[dict[str, str]] = []
    for base in (ROOT / "backend/src", ROOT / "backend/scripts", ROOT / "frontend/src"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for fragment, reason in FORBIDDEN_RUNTIME_FRAGMENTS.items():
                if fragment in text:
                    violations.append(
                        {"path": str(path.relative_to(ROOT)), "reason": reason}
                    )
    add(
        checks,
        "forbidden_runtime_patterns_absent",
        not violations,
        violations or "No forbidden patterns found.",
    )


def main() -> int:
    checks: list[dict[str, Any]] = []
    compile_python(checks)
    validate_frontend(checks)
    validate_provider_layout(checks)
    validate_schema_and_data_console(checks)
    validate_domain_flow(checks)
    validate_seed(checks)
    validate_runtime_boundaries(checks)
    validate_migrations_and_readme(checks)
    validate_forbidden(checks)
    failed = [check for check in checks if check["status"] != "PASS"]
    payload = {
        "stage": "Stage 4 — HLD Alignment and Data Console Completion",
        "validationLevel": "SOURCE_VALIDATED",
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "dockerAvailable": False,
        },
        "command": "python3.13 scripts/validate_stage4_source.py",
        "generatedAt": datetime.now(UTC).isoformat(),
        "exitCode": int(bool(failed)),
        "status": "FAILED" if failed else "PASSED",
        "checks": checks,
        "limitations": [
            "This source gate does not replace Ruff, strict mypy, pytest, Node 24 "
            "dependency-backed checks, Compose startup, SQL/Mongo/Neo4j integration "
            "tests, or live provider validation.",
            "The current audit host has Node 22, no Docker daemon, and no installed "
            "backend runtime dependency set.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, default=str))
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
