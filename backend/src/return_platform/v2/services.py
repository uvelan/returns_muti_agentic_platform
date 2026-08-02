"""Governed V2 backend services with deterministic in-memory persistence boundaries."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import yaml

from return_platform.v2.models import (
    AnchorType,
    ConfigurationModule,
    DraftCreate,
    FieldPatch,
    FullSyncRequest,
    ImportRecord,
    ImportRequest,
    ModuleCreate,
    ModuleStatus,
    OrderAnchor,
    OrderLineProjection,
    OrderProjection,
    PartialSyncRequest,
    ProposalCommand,
    ReleaseCreate,
    ReleaseManifest,
    ReleaseModuleRef,
    ReleaseStatus,
    SchemaAnswer,
    SchemaDesignContext,
    SchemaDesignCreate,
    SchemaQuestion,
    SourceOrderRecord,
    SyncResult,
    SyncStatus,
    ValidationIssue,
    ValidationResult,
    utc_now,
)


class V2ConflictError(RuntimeError):
    """Raised for immutable state, duplicate, or concurrency conflicts."""


class V2NotFoundError(LookupError):
    """Raised when a governed resource does not exist."""


class V2ValidationError(ValueError):
    """Raised when a governed transition fails validation."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _module_checksum(data: Mapping[str, Any]) -> str:
    selected = {
        "moduleId": data["moduleId"],
        "moduleType": data["moduleType"],
        "schemaVersion": data["schemaVersion"],
        "configurationVersion": data["configurationVersion"],
        "owner": data["owner"],
        "dependencies": data.get("dependencies", []),
        "payload": data["payload"],
    }
    return _digest(selected)


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise V2ValidationError(f"Invalid semantic version: {version}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _matches_constraint(version: str, constraint: str) -> bool:
    actual = _version_tuple(version)
    normalized = constraint.strip()
    if normalized.startswith("^"):
        minimum = _version_tuple(normalized[1:] + (".0" if normalized.count(".") == 1 else ""))
        return actual >= minimum and actual[0] == minimum[0]
    if normalized.startswith("="):
        normalized = normalized[1:]
    return actual == _version_tuple(normalized)


def _camelize_module(raw: Mapping[str, Any]) -> dict[str, Any]:
    dependencies = [
        {
            "moduleId": dependency.get("module_id", dependency.get("moduleId")),
            "versionConstraint": dependency.get(
                "version_constraint", dependency.get("versionConstraint")
            ),
        }
        for dependency in raw.get("dependencies", [])
        if isinstance(dependency, Mapping)
    ]
    return {
        "moduleId": raw.get("module_id", raw.get("moduleId")),
        "moduleType": raw.get("module_type", raw.get("moduleType")),
        "schemaVersion": raw.get("schema_version", raw.get("schemaVersion")),
        "configurationVersion": raw.get(
            "configuration_version", raw.get("configurationVersion")
        ),
        "owner": raw.get("owner"),
        "dependencies": dependencies,
        "payload": copy.deepcopy(raw.get("payload", {})),
    }


class ModularConfigurationService:
    """Own immutable module versions and atomic release manifests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._modules: dict[tuple[str, str], ConfigurationModule] = {}
        self._releases: dict[str, ReleaseManifest] = {}
        self._imports: dict[str, ImportRecord] = {}
        self._active_release_id: str | None = None

    def use_order_adapters(
        self, source: OrderSourceGateway, graph: OrderProjectionStore
    ) -> None:
        """Replace degraded in-memory adapters with initialized runtime adapters."""
        self.order_sync = OrderSyncService(source, graph)

    async def bootstrap(self, config_root: Path) -> None:
        manifest_path = config_root / "manifest.yaml"
        if not manifest_path.is_file():
            return
        raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, Mapping):
            raise V2ValidationError("V2 manifest must be an object")
        modules = raw_manifest.get("modules", {})
        if not isinstance(modules, Mapping):
            raise V2ValidationError("V2 manifest modules must be an object")
        for module_id, descriptor in modules.items():
            if not isinstance(module_id, str) or not isinstance(descriptor, Mapping):
                raise V2ValidationError("V2 manifest contains an invalid module entry")
            relative_path = descriptor.get("path")
            if not isinstance(relative_path, str):
                raise V2ValidationError(f"Module {module_id} has no path")
            raw = yaml.safe_load((config_root / relative_path).read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise V2ValidationError(f"Module {module_id} must be an object")
            data = _camelize_module(raw)
            create = ModuleCreate.model_validate(data)
            module = self._build_module(create, "bootstrap", ModuleStatus.DRAFT)
            self._modules[(module.module_id, module.configuration_version)] = module

    def _build_module(
        self, create: ModuleCreate, actor: str, status: ModuleStatus
    ) -> ConfigurationModule:
        data = create.model_dump(mode="json", by_alias=True)
        return ConfigurationModule.model_validate(
            {
                **data,
                "status": status,
                "checksum": _module_checksum(data),
                "createdBy": actor,
                "revision": 1,
            }
        )

    async def module_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "moduleType": module_type,
                "schemaVersion": "1.0",
                "schema": ModuleCreate.model_json_schema(by_alias=True),
                "ui": {"editor": "TYPED_FIELDS", "rawEditingAllowed": False},
            }
            for module_type in sorted({item.module_type for item in self._modules.values()})
        ]

    async def list_modules(
        self, module_type: str | None = None, status: ModuleStatus | None = None
    ) -> list[ConfigurationModule]:
        values = [
            copy.deepcopy(module)
            for module in self._modules.values()
            if (module_type is None or module.module_type == module_type)
            and (status is None or module.status == status)
        ]
        return sorted(values, key=lambda item: (item.module_id, _version_tuple(item.configuration_version)))

    async def get_module(self, module_id: str, version: str) -> ConfigurationModule:
        module = self._modules.get((module_id, version))
        if module is None:
            raise V2NotFoundError(f"Module {module_id}@{version} was not found")
        return copy.deepcopy(module)

    async def create_module(self, create: ModuleCreate, actor: str) -> ConfigurationModule:
        key = (create.module_id, create.configuration_version)
        async with self._lock:
            if key in self._modules:
                raise V2ConflictError(f"Module {create.module_id}@{create.configuration_version} exists")
            module = self._build_module(create, actor, ModuleStatus.DRAFT)
            self._modules[key] = module
            return copy.deepcopy(module)

    async def create_draft(
        self, module_id: str, request: DraftCreate, actor: str
    ) -> ConfigurationModule:
        versions = [item for (identifier, _), item in self._modules.items() if identifier == module_id]
        if not versions:
            raise V2NotFoundError(f"Module {module_id} was not found")
        source_version = request.from_version
        source = (
            await self.get_module(module_id, source_version)
            if source_version
            else max(versions, key=lambda item: _version_tuple(item.configuration_version))
        )
        create = ModuleCreate(
            module_id=source.module_id,
            module_type=source.module_type,
            schema_version=source.schema_version,
            configuration_version=request.configuration_version,
            owner=source.owner,
            dependencies=source.dependencies,
            payload=copy.deepcopy(source.payload),
        )
        return await self.create_module(create, actor)

    async def patch_fields(
        self, module_id: str, version: str, patch: FieldPatch, actor: str
    ) -> ConfigurationModule:
        del actor
        key = (module_id, version)
        async with self._lock:
            current = self._modules.get(key)
            if current is None:
                raise V2NotFoundError(f"Module {module_id}@{version} was not found")
            if current.status not in {ModuleStatus.DRAFT, ModuleStatus.QUARANTINED}:
                raise V2ConflictError("Only draft or quarantined modules can be edited")
            if current.revision != patch.expected_revision:
                raise V2ConflictError(
                    f"Revision conflict: expected {patch.expected_revision}, current {current.revision}"
                )
            payload = copy.deepcopy(current.payload)
            self._apply_field_patch(payload, patch)
            data = current.model_dump(mode="json", by_alias=True)
            data["payload"] = payload
            data["revision"] = current.revision + 1
            data["status"] = ModuleStatus.DRAFT
            data["checksum"] = _module_checksum(data)
            updated = ConfigurationModule.model_validate(data)
            self._modules[key] = updated
            return copy.deepcopy(updated)

    @staticmethod
    def _apply_field_patch(payload: dict[str, Any], patch: FieldPatch) -> None:
        cursor: Any = payload
        for segment in patch.path[:-1]:
            if isinstance(segment, int):
                if not isinstance(cursor, list) or segment >= len(cursor):
                    raise V2ValidationError("Field path does not exist")
                cursor = cursor[segment]
            else:
                if not isinstance(cursor, dict):
                    raise V2ValidationError("Field path traverses a scalar value")
                cursor = cursor.setdefault(segment, {})
        leaf = patch.path[-1]
        if patch.operation == "APPEND":
            target = cursor[leaf] if isinstance(leaf, (str, int)) else None
            if not isinstance(target, list):
                raise V2ValidationError("APPEND requires an existing list field")
            target.append(copy.deepcopy(patch.value))
        elif patch.operation == "REMOVE":
            if isinstance(cursor, dict) and isinstance(leaf, str):
                if leaf not in cursor:
                    raise V2ValidationError("Field path does not exist")
                del cursor[leaf]
            elif isinstance(cursor, list) and isinstance(leaf, int) and leaf < len(cursor):
                cursor.pop(leaf)
            else:
                raise V2ValidationError("Field path does not exist")
        elif isinstance(cursor, dict) and isinstance(leaf, str):
            cursor[leaf] = copy.deepcopy(patch.value)
        elif isinstance(cursor, list) and isinstance(leaf, int) and leaf < len(cursor):
            cursor[leaf] = copy.deepcopy(patch.value)
        else:
            raise V2ValidationError("Field path does not exist")

    async def validate_module(self, module_id: str, version: str) -> ValidationResult:
        module = await self.get_module(module_id, version)
        issues: list[ValidationIssue] = []
        if not module.payload:
            issues.append(
                ValidationIssue(code="EMPTY_PAYLOAD", path=("payload",), message="Payload is empty")
            )
        if module.module_type == "AGENT":
            if module.payload.get("direct_agent_calls_allowed") is not False:
                issues.append(
                    ValidationIssue(
                        code="AGENT_INDEPENDENCE_REQUIRED",
                        path=("payload", "direct_agent_calls_allowed"),
                        message="Agents must not call other business agents directly",
                    )
                )
            if module.payload.get("idempotency_required") is not True:
                issues.append(
                    ValidationIssue(
                        code="IDEMPOTENCY_REQUIRED",
                        path=("payload", "idempotency_required"),
                        message="Agent execution must be idempotent",
                    )
                )
        for dependency in module.dependencies:
            if not any(identifier == dependency.module_id for identifier, _ in self._modules):
                issues.append(
                    ValidationIssue(
                        code="DEPENDENCY_MISSING",
                        path=("dependencies", dependency.module_id),
                        message=f"Dependency {dependency.module_id} is not configured",
                    )
                )
        return ValidationResult(valid=not issues, issues=tuple(issues), checksum=module.checksum)

    async def transition_module(
        self, module_id: str, version: str, target: ModuleStatus
    ) -> ConfigurationModule:
        allowed = {
            ModuleStatus.DRAFT: {ModuleStatus.VALIDATED, ModuleStatus.ARCHIVED},
            ModuleStatus.QUARANTINED: {ModuleStatus.DRAFT, ModuleStatus.ARCHIVED},
            ModuleStatus.VALIDATED: {ModuleStatus.APPROVED, ModuleStatus.DRAFT, ModuleStatus.ARCHIVED},
            ModuleStatus.APPROVED: {ModuleStatus.RELEASED, ModuleStatus.ARCHIVED},
            ModuleStatus.RELEASED: {ModuleStatus.SUPERSEDED},
            ModuleStatus.SUPERSEDED: {ModuleStatus.ARCHIVED},
        }
        async with self._lock:
            current = self._modules.get((module_id, version))
            if current is None:
                raise V2NotFoundError(f"Module {module_id}@{version} was not found")
            if target not in allowed.get(current.status, set()):
                raise V2ConflictError(f"Invalid module transition {current.status} -> {target}")
            if target in {ModuleStatus.VALIDATED, ModuleStatus.APPROVED}:
                result = await self.validate_module(module_id, version)
                if not result.valid:
                    raise V2ValidationError("Module validation failed")
            updated = current.model_copy(update={"status": target})
            self._modules[(module_id, version)] = updated
            return copy.deepcopy(updated)

    async def create_release(self, create: ReleaseCreate, actor: str) -> ReleaseManifest:
        async with self._lock:
            if create.release_id in self._releases:
                raise V2ConflictError(f"Release {create.release_id} exists")
            manifest = ReleaseManifest(
                release_id=create.release_id,
                status=ReleaseStatus.DRAFT,
                modules=create.modules,
                dependency_lock_digest=_digest(
                    [item.model_dump(mode="json", by_alias=True) for item in create.modules]
                ),
                created_by=actor,
            )
            self._releases[manifest.release_id] = manifest
            return copy.deepcopy(manifest)

    async def get_release(self, release_id: str) -> ReleaseManifest:
        release = self._releases.get(release_id)
        if release is None:
            raise V2NotFoundError(f"Release {release_id} was not found")
        return copy.deepcopy(release)

    async def list_releases(self) -> list[ReleaseManifest]:
        return sorted(
            (copy.deepcopy(item) for item in self._releases.values()),
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def validate_release(self, release_id: str) -> ValidationResult:
        release = await self.get_release(release_id)
        issues: list[ValidationIssue] = []
        selected = {reference.module_id: reference for reference in release.modules}
        for reference in release.modules:
            module = self._modules.get((reference.module_id, reference.version))
            if module is None:
                issues.append(
                    ValidationIssue(
                        code="MODULE_MISSING",
                        path=("modules", reference.module_id),
                        message=f"Module {reference.module_id}@{reference.version} does not exist",
                    )
                )
                continue
            if module.checksum != reference.checksum:
                issues.append(
                    ValidationIssue(
                        code="CHECKSUM_MISMATCH",
                        path=("modules", reference.module_id, "checksum"),
                        message=f"Checksum does not match {reference.module_id}@{reference.version}",
                    )
                )
            if module.status not in {ModuleStatus.APPROVED, ModuleStatus.RELEASED}:
                issues.append(
                    ValidationIssue(
                        code="MODULE_NOT_APPROVED",
                        path=("modules", reference.module_id),
                        message=f"Module {reference.module_id} is {module.status}",
                    )
                )
            for dependency in module.dependencies:
                chosen = selected.get(dependency.module_id)
                if chosen is None:
                    issues.append(
                        ValidationIssue(
                            code="DEPENDENCY_NOT_LOCKED",
                            path=("modules", reference.module_id, "dependencies"),
                            message=f"Dependency {dependency.module_id} is absent from release",
                        )
                    )
                elif not _matches_constraint(chosen.version, dependency.version_constraint):
                    issues.append(
                        ValidationIssue(
                            code="DEPENDENCY_INCOMPATIBLE",
                            path=("modules", dependency.module_id),
                            message=(
                                f"Version {chosen.version} does not satisfy "
                                f"{dependency.version_constraint}"
                            ),
                        )
                    )
        issues.extend(self._cycle_issues(selected))
        return ValidationResult(
            valid=not issues,
            issues=tuple(issues),
            checksum=release.dependency_lock_digest,
        )

    def _cycle_issues(self, selected: Mapping[str, ReleaseModuleRef]) -> list[ValidationIssue]:
        graph: dict[str, set[str]] = {}
        for module_id, reference in selected.items():
            module = self._modules.get((module_id, reference.version))
            graph[module_id] = (
                {item.module_id for item in module.dependencies if item.module_id in selected}
                if module
                else set()
            )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            cyclic = any(visit(dependency) for dependency in graph.get(node, set()))
            visiting.remove(node)
            visited.add(node)
            return cyclic

        return [
            ValidationIssue(
                code="DEPENDENCY_CYCLE",
                path=("modules",),
                message="Release module dependencies contain a cycle",
            )
        ] if any(visit(node) for node in graph) else []

    async def transition_release(
        self, release_id: str, target: ReleaseStatus
    ) -> ReleaseManifest:
        allowed = {
            ReleaseStatus.DRAFT: {ReleaseStatus.DEPENDENCIES_RESOLVED, ReleaseStatus.ARCHIVED},
            ReleaseStatus.DEPENDENCIES_RESOLVED: {ReleaseStatus.VALIDATED, ReleaseStatus.DRAFT},
            ReleaseStatus.VALIDATED: {ReleaseStatus.APPROVED, ReleaseStatus.DRAFT},
            ReleaseStatus.APPROVED: {ReleaseStatus.MIGRATION_READY},
            ReleaseStatus.MIGRATION_READY: {ReleaseStatus.ACTIVE},
            ReleaseStatus.ACTIVE: {ReleaseStatus.SUPERSEDED},
            ReleaseStatus.SUPERSEDED: {ReleaseStatus.ARCHIVED},
        }
        async with self._lock:
            current = self._releases.get(release_id)
            if current is None:
                raise V2NotFoundError(f"Release {release_id} was not found")
            if target not in allowed.get(current.status, set()):
                raise V2ConflictError(f"Invalid release transition {current.status} -> {target}")
            if target in {
                ReleaseStatus.DEPENDENCIES_RESOLVED,
                ReleaseStatus.VALIDATED,
                ReleaseStatus.APPROVED,
                ReleaseStatus.MIGRATION_READY,
                ReleaseStatus.ACTIVE,
            }:
                validation = await self.validate_release(release_id)
                if not validation.valid:
                    raise V2ValidationError("Release validation failed")
            if target is ReleaseStatus.ACTIVE:
                previous_id = self._active_release_id
                if previous_id and previous_id != release_id:
                    previous = self._releases[previous_id]
                    self._releases[previous_id] = previous.model_copy(
                        update={"status": ReleaseStatus.SUPERSEDED}
                    )
                self._active_release_id = release_id
            updated = current.model_copy(
                update={"status": target, "activated_at": utc_now() if target is ReleaseStatus.ACTIVE else None}
            )
            self._releases[release_id] = updated
            return copy.deepcopy(updated)

    async def active_release(self) -> ReleaseManifest | None:
        return (
            await self.get_release(self._active_release_id)
            if self._active_release_id is not None
            else None
        )

    async def export_module(self, module_id: str, version: str, format_name: str) -> str:
        module = await self.get_module(module_id, version)
        payload = module.model_dump(mode="json", by_alias=True)
        return (
            yaml.safe_dump(payload, sort_keys=False)
            if format_name.upper() == "YAML"
            else json.dumps(payload, indent=2, sort_keys=True)
        )

    async def import_modules(self, request: ImportRequest, actor: str) -> ImportRecord:
        import_id = str(uuid.uuid4())
        issues: list[ValidationIssue] = []
        modules: list[ConfigurationModule] = []
        try:
            parsed = (
                json.loads(request.content)
                if request.format == "JSON"
                else yaml.safe_load(request.content)
            )
            candidates = parsed if isinstance(parsed, list) else [parsed]
            if not candidates or any(not isinstance(item, Mapping) for item in candidates):
                raise ValueError("Import must contain a module object or list of module objects")
            forbidden_pattern = re.compile(
                r"(?:<script|\bexec\s*\(|\beval\s*\(|\bMATCH\s*\(|\bDROP\s+TABLE\b)", re.I
            )
            if forbidden_pattern.search(request.content):
                raise ValueError("Import contains executable or prohibited statements")
            for item in candidates:
                assert isinstance(item, Mapping)
                data = dict(item)
                create = ModuleCreate.model_validate(data)
                modules.append(self._build_module(create, actor, ModuleStatus.QUARANTINED))
        except Exception as exc:
            issues.append(
                ValidationIssue(code="IMPORT_REJECTED", message=str(exc)[:500], path=())
            )
        record = ImportRecord(
            import_id=import_id,
            status="REJECTED" if issues else "QUARANTINED",
            modules=tuple(modules),
            issues=tuple(issues),
            created_by=actor,
        )
        self._imports[import_id] = record
        return copy.deepcopy(record)

    async def get_import(self, import_id: str) -> ImportRecord:
        record = self._imports.get(import_id)
        if record is None:
            raise V2NotFoundError(f"Import {import_id} was not found")
        return copy.deepcopy(record)

    async def create_import_drafts(self, import_id: str) -> ImportRecord:
        async with self._lock:
            record = self._imports.get(import_id)
            if record is None:
                raise V2NotFoundError(f"Import {import_id} was not found")
            if record.status != "QUARANTINED":
                raise V2ConflictError("Only a quarantined import can create drafts")
            for module in record.modules:
                key = (module.module_id, module.configuration_version)
                if key in self._modules:
                    raise V2ConflictError(f"Module {module.module_id}@{module.configuration_version} exists")
                self._modules[key] = module.model_copy(update={"status": ModuleStatus.DRAFT})
            updated = record.model_copy(update={"status": "DRAFTS_CREATED"})
            self._imports[import_id] = updated
            return copy.deepcopy(updated)


class SchemaDesignService:
    """Stateless-per-context graph schema assistant with governed proposal commands."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._contexts: dict[str, SchemaDesignContext] = {}

    async def create(self, request: SchemaDesignCreate, actor: str) -> SchemaDesignContext:
        request_id = str(uuid.uuid4())
        context = SchemaDesignContext(
            request_id=request_id,
            context_version=1,
            selected_modules=request.selected_modules,
            requested_capabilities=request.requested_capabilities,
            source_structures=request.source_structures,
            existing_schema=request.existing_schema,
            status="ANALYZING",
            created_by=actor,
        )
        context = self._analyze(context)
        self._contexts[request_id] = context
        return copy.deepcopy(context)

    async def get(self, request_id: str) -> SchemaDesignContext:
        context = self._contexts.get(request_id)
        if context is None:
            raise V2NotFoundError(f"Schema design request {request_id} was not found")
        return copy.deepcopy(context)

    def _gaps(self, context: SchemaDesignContext) -> list[SchemaQuestion]:
        gaps: list[SchemaQuestion] = []
        for structure in context.source_structures:
            paths = {field.path for field in structure.fields}
            if not structure.identity_paths:
                question_id = _digest([structure.source_id, structure.dataset, "identity"])[:20]
                if question_id not in context.answers:
                    candidates = tuple(
                        field.path
                        for field in structure.fields
                        if field.key or field.path.lower().endswith(("id", "_id", "number"))
                    )
                    gaps.append(
                        SchemaQuestion(
                            question_id=question_id,
                            field_path=f"sources.{structure.source_id}.{structure.dataset}.identityPaths",
                            prompt=f"Which immutable field or composite fields identify one {structure.dataset} record?",
                            reason="A stable identity is required before graph nodes can be synchronized safely.",
                            required_owner="SOURCE_OWNER",
                            evidence=(f"No identity path exists in fingerprint {structure.fingerprint}",),
                            options=candidates[:6],
                        )
                    )
            if any(field.sensitive for field in structure.fields):
                question_id = _digest([structure.source_id, structure.dataset, "privacy"])[:20]
                if question_id not in context.answers and not (
                    context.existing_schema or {}
                ).get("privacyPolicyRef"):
                    sensitive = tuple(field.path for field in structure.fields if field.sensitive)
                    gaps.append(
                        SchemaQuestion(
                            question_id=question_id,
                            field_path="graph.privacyPolicyRef",
                            prompt="Which approved privacy policy governs the sensitive source fields used by this schema?",
                            reason="Sensitive properties cannot be projected without an explicit policy reference.",
                            required_owner="SECURITY_OWNER",
                            evidence=tuple(f"Sensitive field: {path}" for path in sensitive[:5]),
                        )
                    )
            requested_relationship = any("relationship" in item.lower() for item in context.requested_capabilities)
            if requested_relationship and not structure.candidate_joins:
                question_id = _digest([structure.source_id, structure.dataset, "join"])[:20]
                if question_id not in context.answers:
                    options = tuple(sorted(path for path in paths if "id" in path.lower()))[:6]
                    gaps.append(
                        SchemaQuestion(
                            question_id=question_id,
                            field_path=f"sources.{structure.source_id}.{structure.dataset}.candidateJoins",
                            prompt=f"Which governed join connects {structure.dataset} to the requested related entity?",
                            reason="Relationship cardinality and join fields must be explicit.",
                            required_owner="DATA_STEWARD",
                            evidence=("Requested capability requires a relationship",),
                            options=options,
                        )
                    )
        return gaps

    def _commands(self, context: SchemaDesignContext) -> tuple[ProposalCommand, ...]:
        commands: list[ProposalCommand] = []
        for structure in context.source_structures:
            identity_question = _digest([structure.source_id, structure.dataset, "identity"])[:20]
            identity = context.answers.get(identity_question) or list(structure.identity_paths)
            if identity:
                commands.append(
                    ProposalCommand(
                        module_id="graph.generated_schema",
                        path=("payload", "nodes", structure.dataset, "uniqueKey"),
                        operation="SET",
                        proposed_value=identity,
                        evidence=(f"Source fingerprint {structure.fingerprint}",),
                        reason="Create a stable canonical node identity",
                        change_classification="NON_BREAKING" if context.existing_schema is None else "BREAKING_REQUIRES_MAJOR_VERSION",
                        required_owner="ARCHITECT",
                    )
                )
        return tuple(commands)

    def _analyze(self, context: SchemaDesignContext) -> SchemaDesignContext:
        gaps = self._gaps(context)
        commands = self._commands(context)
        return context.model_copy(
            update={
                "commands": commands,
                "current_question": gaps[0] if gaps else None,
                "status": "WAITING_FOR_ANSWER" if gaps else "REVIEW_READY",
                "updated_at": utc_now(),
            }
        )

    async def next_question(self, request_id: str) -> SchemaDesignContext:
        context = await self.get(request_id)
        updated = self._analyze(context)
        self._contexts[request_id] = updated
        return copy.deepcopy(updated)

    async def answer(self, request_id: str, answer: SchemaAnswer) -> SchemaDesignContext:
        async with self._lock:
            context = self._contexts.get(request_id)
            if context is None:
                raise V2NotFoundError(f"Schema design request {request_id} was not found")
            question = context.current_question
            if question is None or question.question_id != answer.question_id:
                raise V2ConflictError("Answer does not match the current unresolved question")
            answers = copy.deepcopy(context.answers)
            answers[answer.question_id] = copy.deepcopy(answer.value)
            updated = context.model_copy(
                update={"answers": answers, "context_version": context.context_version + 1}
            )
            updated = self._analyze(updated)
            self._contexts[request_id] = updated
            return copy.deepcopy(updated)

    async def validate(self, request_id: str) -> ValidationResult:
        context = await self.get(request_id)
        gaps = self._gaps(context)
        issues = tuple(
            ValidationIssue(
                code="SCHEMA_DECISION_REQUIRED",
                path=tuple(question.field_path.split(".")),
                message=question.reason,
                suggested_resolution=question.prompt,
            )
            for question in gaps
        )
        return ValidationResult(valid=not issues and bool(context.commands), issues=issues)

    async def simulate(self, request_id: str) -> dict[str, Any]:
        context = await self.get(request_id)
        validation = await self.validate(request_id)
        if not validation.valid:
            raise V2ValidationError("Schema proposal is not review-ready")
        return {
            "requestId": request_id,
            "contextVersion": context.context_version,
            "status": "SIMULATION_PASSED",
            "commandsEvaluated": len(context.commands),
            "productionWrites": 0,
            "activationPerformed": False,
            "migrationPerformed": False,
            "digest": _digest([item.model_dump(mode="json") for item in context.commands]),
        }


class OrderSourceGateway(Protocol):
    async def resolve(self, anchor: OrderAnchor, limit: int) -> list[str]: ...
    async def fetch(self, full_order_id: str) -> SourceOrderRecord | None: ...


class OrderProjectionStore(Protocol):
    async def upsert_candidates(self, orders: Sequence[OrderProjection]) -> int: ...
    async def replace_full_order(self, order: OrderProjection) -> int: ...
    async def get(self, full_order_id: str) -> OrderProjection | None: ...


class InMemoryOrderSourceGateway:
    def __init__(self, records: Sequence[SourceOrderRecord] = ()) -> None:
        self._records = {self.full_order_id(item): copy.deepcopy(item) for item in records}

    @staticmethod
    def full_order_id(record: SourceOrderRecord) -> str:
        return f"{record.account.strip().upper()}*{record.order_number.strip().upper()}"

    async def replace_records(self, records: Sequence[SourceOrderRecord]) -> None:
        self._records = {self.full_order_id(item): copy.deepcopy(item) for item in records}

    async def resolve(self, anchor: OrderAnchor, limit: int) -> list[str]:
        value = anchor.value.strip().upper()
        matches: list[str] = []
        for full_order_id, record in self._records.items():
            if anchor.account_scope and record.account.upper() != anchor.account_scope.upper():
                continue
            matched = (
                (anchor.type is AnchorType.FULL_ORDER_ID and full_order_id == value)
                or (
                    anchor.type is AnchorType.ORDER_REFERENCE
                    and (record.order_number.upper() == value or full_order_id == value)
                )
                or (
                    anchor.type is AnchorType.TRACKING_NUMBER
                    and value in {item.upper() for item in record.tracking_numbers}
                )
                or (
                    anchor.type is AnchorType.INVOICE_NUMBER
                    and value in {item.upper() for item in record.invoice_numbers}
                )
                or (
                    anchor.type is AnchorType.DELIVERY_TICKET
                    and (record.delivery_ticket or "").upper() == value
                )
                or (
                    anchor.type is AnchorType.CUSTOMER_PO
                    and (record.customer_po or "").upper() == value
                )
            )
            if matched:
                matches.append(full_order_id)
                if len(matches) >= limit:
                    break
        return matches

    async def fetch(self, full_order_id: str) -> SourceOrderRecord | None:
        record = self._records.get(full_order_id.upper())
        return copy.deepcopy(record) if record else None


class InMemoryOrderProjectionStore:
    def __init__(self) -> None:
        self._orders: dict[str, OrderProjection] = {}
        self._lock = asyncio.Lock()

    async def upsert_candidates(self, orders: Sequence[OrderProjection]) -> int:
        async with self._lock:
            for order in orders:
                self._orders[order.full_order_id] = copy.deepcopy(order)
        return len(orders)

    async def replace_full_order(self, order: OrderProjection) -> int:
        async with self._lock:
            self._orders[order.full_order_id] = copy.deepcopy(order)
        return 1 + len(order.lines)

    async def get(self, full_order_id: str) -> OrderProjection | None:
        order = self._orders.get(full_order_id)
        return copy.deepcopy(order) if order else None


class OrderSyncService:
    """Resolve strong anchors and synchronize minimal canonical order projections."""

    _FULL_ORDER_PATTERN = re.compile(r"^([^*\s]+)\*([^*\s]+)$")

    def __init__(
        self, source: OrderSourceGateway, graph: OrderProjectionStore
    ) -> None:
        self._source = source
        self._graph = graph
        self._results: dict[str, SyncResult] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def normalize_full_order_id(cls, value: str) -> str:
        normalized = value.strip().upper()
        match = cls._FULL_ORDER_PATTERN.fullmatch(normalized)
        if match is None:
            raise V2ValidationError(
                "fullOrderId must contain exactly ACCOUNT_OR_LOGON*ORDERNUMBER"
            )
        return f"{match.group(1)}*{match.group(2)}"

    @staticmethod
    def _authorized(full_order_id: str, accounts: Sequence[str]) -> bool:
        return not accounts or full_order_id.split("*", 1)[0] in {
            item.strip().upper() for item in accounts
        }

    @classmethod
    def _projection(cls, record: SourceOrderRecord, include_lines: bool) -> OrderProjection:
        full_order_id = cls.normalize_full_order_id(f"{record.account}*{record.order_number}")
        lines: list[OrderLineProjection] = []
        if include_lines:
            for raw in record.lines:
                number = str(raw["lineNumber"]).strip()
                line_id = f"{full_order_id}*{number}"
                lines.append(
                    OrderLineProjection(
                        full_order_line_id=line_id,
                        line_number=number,
                        item_number=(str(raw["itemNumber"]) if raw.get("itemNumber") is not None else None),
                        description=(str(raw["description"]) if raw.get("description") is not None else None),
                        quantity_ordered=raw.get("quantityOrdered"),
                        quantity_returned=raw.get("quantityReturned"),
                    )
                )
        return OrderProjection(
            full_order_id=full_order_id,
            account=record.account,
            order_number=record.order_number,
            customer_id=record.customer_id,
            customer_name=record.customer_name,
            customer_po=record.customer_po,
            delivery_ticket=record.delivery_ticket,
            invoice_numbers=record.invoice_numbers,
            tracking_numbers=record.tracking_numbers,
            source_revision=record.source_revision,
            lines=tuple(lines),
        )

    async def _cached(self, idempotency_key: str) -> SyncResult | None:
        request_id = self._idempotency.get(idempotency_key)
        return copy.deepcopy(self._results[request_id]) if request_id else None

    async def partial(self, request: PartialSyncRequest) -> SyncResult:
        cached = await self._cached(request.idempotency_key)
        if cached:
            return cached
        limit = request.authorization_scope.max_candidates + 1
        resolved = await self._source.resolve(request.anchor, limit)
        authorized = sorted(
            {
                self.normalize_full_order_id(item)
                for item in resolved
                if self._authorized(item, request.authorization_scope.accounts)
            }
        )
        capped = len(authorized) > request.authorization_scope.max_candidates
        selected = authorized[: request.authorization_scope.max_candidates]
        orders: list[OrderProjection] = []
        for full_order_id in selected:
            record = await self._source.fetch(full_order_id)
            if record is not None:
                orders.append(self._projection(record, include_lines=False))
        writes = 0 if capped else await self._graph.upsert_candidates(orders)
        status = (
            SyncStatus.NARROWING_REQUIRED
            if capped
            else SyncStatus.RESOLVED
            if orders
            else SyncStatus.NOT_FOUND
        )
        result = SyncResult(
            request_id=str(uuid.uuid4()),
            sync_type="PARTIAL_ORDER_SYNC",
            status=status,
            release_id=request.release_id,
            full_order_ids=tuple(selected),
            orders=tuple(orders) if not capped else (),
            records_read=len(orders),
            graph_writes=writes,
            message=(
                "Candidate cap exceeded; additional narrowing is required"
                if capped
                else "Order candidates synchronized"
                if orders
                else "No authorized orders matched the anchor"
            ),
            digest=_digest([item.model_dump(mode="json") for item in orders]),
        )
        return await self._store(request.idempotency_key, result)

    async def full(self, request: FullSyncRequest) -> SyncResult:
        cached = await self._cached(request.idempotency_key)
        if cached:
            return cached
        full_order_id = self.normalize_full_order_id(request.full_order_id)
        if not self._authorized(full_order_id, request.authorization_scope.accounts):
            result = SyncResult(
                request_id=str(uuid.uuid4()), sync_type="FULL_ORDER_SYNC",
                status=SyncStatus.REJECTED, release_id=request.release_id,
                message="Order is outside the authorized account scope", digest=_digest([]),
            )
            return await self._store(request.idempotency_key, result)
        record = await self._source.fetch(full_order_id)
        if record is None:
            result = SyncResult(
                request_id=str(uuid.uuid4()), sync_type="FULL_ORDER_SYNC",
                status=SyncStatus.NOT_FOUND, release_id=request.release_id,
                full_order_ids=(full_order_id,), message="Order was not found", digest=_digest([]),
            )
            return await self._store(request.idempotency_key, result)
        projection = self._projection(record, include_lines=True)
        if projection.full_order_id != full_order_id:
            raise V2ValidationError("Source record belongs to another normalized parent order")
        writes = await self._graph.replace_full_order(projection)
        readback = await self._graph.get(full_order_id)
        if readback is None or _digest(readback.model_dump(mode="json")) != _digest(
            projection.model_dump(mode="json")
        ):
            raise V2ValidationError("Graph readback verification failed")
        result = SyncResult(
            request_id=str(uuid.uuid4()), sync_type="FULL_ORDER_SYNC",
            status=SyncStatus.COMPLETED, release_id=request.release_id,
            full_order_ids=(full_order_id,), orders=(projection,), records_read=1 + len(record.lines),
            graph_writes=writes, message="Full order and all authoritative lines synchronized",
            digest=_digest(projection.model_dump(mode="json")),
        )
        return await self._store(request.idempotency_key, result)

    async def _store(self, idempotency_key: str, result: SyncResult) -> SyncResult:
        async with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id:
                return copy.deepcopy(self._results[existing_id])
            self._idempotency[idempotency_key] = result.request_id
            self._results[result.request_id] = result
            return copy.deepcopy(result)

    async def get(self, request_id: str) -> SyncResult:
        result = self._results.get(request_id)
        if result is None:
            raise V2NotFoundError(f"Order sync request {request_id} was not found")
        return copy.deepcopy(result)


class V2PlatformServices:
    """Application-owned aggregate for all V2 service domains."""

    def __init__(self) -> None:
        source = InMemoryOrderSourceGateway()
        self.configuration = ModularConfigurationService()
        self.schema_design = SchemaDesignService()
        self.order_source = source
        self.order_sync = OrderSyncService(source, InMemoryOrderProjectionStore())

    def use_order_adapters(
        self, source: OrderSourceGateway, graph: OrderProjectionStore
    ) -> None:
        """Replace degraded in-memory adapters with initialized runtime adapters."""
        self.order_sync = OrderSyncService(source, graph)

    async def bootstrap(self, config_root: Path) -> None:
        await self.configuration.bootstrap(config_root)
