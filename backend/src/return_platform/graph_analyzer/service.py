from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import pymssql
from neo4j import WRITE_ACCESS, AsyncDriver
from pymongo import AsyncMongoClient, ReturnDocument

from return_platform.configuration.settings import Settings
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry
from return_platform.graph_analyzer.agent_port import AgentReasoningPort
from return_platform.graph_analyzer.analysis import (
    MAX_OBJECTS_PER_ANALYSIS,
    ObjectEvidence,
    deterministic_proposal,
    gather_evidence,
    ground_proposal,
    reasoned_proposal,
)
from return_platform.graph_analyzer.discovery import (
    count_objects,
    discover_objects,
    graph_sample,
    probe_source,
    sample_object,
)
from return_platform.graph_analyzer.models import (
    AgentMessage,
    AgentRecommendation,
    AgentReply,
    AgentRequest,
    AnalysisRequest,
    AnalysisRun,
    AnalyzerBootstrap,
    AnalyzerGraphSchema,
    AnalyzerSource,
    GraphEntity,
    GraphProperty,
    GraphRelationship,
    PreviewPage,
    RecommendationResult,
    SchemaValidation,
    SourceField,
    SourceInput,
    SourceObject,
    SyncRequest,
    SyncRun,
    ValidationIssue,
)
from return_platform.graph_analyzer.safety import assert_system_graph_target
from return_platform.graph_schema_analyzer.application.prompt_context import (
    build_prompt_blocks,
    neutralize_delimiters,
)
from return_platform.graph_schema_analyzer.ports.ai_port import SchemaReasoningPort

logger = logging.getLogger("return_platform.graph_analyzer")


class AgentUnavailableError(RuntimeError):
    """Raised when no AI route is configured for the Analyzer Agent."""


_AGENT_TASK_DEFINITION = (
    "Answer the operator's question about the source structure, the proposed system "
    "graph, its validation, or its synchronization. You may propose at most one change, "
    "and it may target only the system graph proposal. Never propose or describe a change "
    "to a source system: sources are read-only evidence."
)

#: Hard ceiling on how deep preview paging may read into a source object.
#: Preview is for understanding shape, not for exporting a table.
MAX_PREVIEW_ROWS = 2_000

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_name(value: str, fallback: str = "Entity") -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    candidate = "".join(word[:1].upper() + word[1:] for word in words) or fallback
    return candidate if candidate[0].isalpha() else f"{fallback}{candidate}"


def _canvas_position(index: int, total: int) -> tuple[float, float]:
    """Place entity `index` of `total` on the 0-100 canvas.

    `GraphEntity.x` and `.y` are percentages of the canvas, bounded 0..100. The
    layout used to be a fixed three-column grid with a constant row pitch --
    `y = 25 + (index // 3) * 35` -- which leaves the canvas on the fourth row:
    at index 9 that is 130, and the model rejects it, so `GET /bootstrap`
    answered 500 on any graph with ten or more entities. It passed everywhere
    the fixture graphs were small enough, which is every test.

    The grid is therefore derived from the count instead of fixed: a roughly
    square arrangement, spread evenly between margins, in bounds for any total.
    """
    total = max(total, 1)
    columns = math.ceil(math.sqrt(total))
    rows = math.ceil(total / columns)
    margin = 10.0
    span = 100.0 - 2 * margin
    # One column (or row) sits centred rather than hard against the margin.
    step_x = span / max(columns - 1, 1) if columns > 1 else 0.0
    step_y = span / max(rows - 1, 1) if rows > 1 else 0.0
    x = margin + (index % columns) * step_x if columns > 1 else 50.0
    y = margin + (index // columns) * step_y if rows > 1 else 50.0
    return round(x, 2), round(y, 2)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


class GraphAnalyzerService:
    """Portable analyzer core with a hard source-read/system-graph-write boundary."""

    def __init__(
        self,
        *,
        platform_client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        graph_driver: AsyncDriver,
        settings: Settings,
        registry: SchemaRegistry,
        reasoning: SchemaReasoningPort | None = None,
        agent: AgentReasoningPort | None = None,
    ) -> None:
        self._settings = settings
        self._reasoning = reasoning
        self._agent = agent
        self._registry = registry
        self._source_client = source_client
        self._graph_driver = graph_driver
        database = platform_client[settings.mongo_database]
        self._sources = database["graph_analyzer_sources"]
        self._schemas = database["graph_analyzer_schemas"]
        self._analyses = database["graph_analyzer_analyses"]
        self._validations = database["graph_analyzer_validations"]
        self._recommendations = database["graph_analyzer_recommendations"]
        self._sync_runs = database["graph_analyzer_sync_runs"]

    async def ensure_indexes(self) -> None:
        await asyncio.gather(
            self._sources.create_index("name", unique=True),
            self._schemas.create_index([("kind", 1), ("version", -1)]),
            self._analyses.create_index([("startedAt", -1)]),
            self._sync_runs.create_index([("startedAt", -1)]),
        )

    def _source_objects(
        self, engine: str, database: str, assets: list[DataAssetSchema]
    ) -> list[SourceObject]:
        grouped: dict[str, list[DataAssetSchema]] = {}
        for asset in assets:
            grouped.setdefault(asset.namespace or "Collections", []).append(asset)
        children: list[SourceObject] = []
        for namespace, namespace_assets in sorted(grouped.items()):
            object_children = [
                SourceObject(
                    id=asset.asset_id,
                    name=asset.name,
                    kind="table" if engine == "SQLSERVER" else "collection",
                    path=[database, namespace, asset.name],
                    selectable=True,
                    fields=[
                        SourceField(
                            name=field.name,
                            dataType=field.type,
                            nullable=not field.required,
                            identifier=field.key,
                            indexed=field.key,
                        )
                        for field in asset.fields
                    ],
                )
                for asset in sorted(namespace_assets, key=lambda item: item.name)
            ]
            children.append(
                SourceObject(
                    id=f"namespace:{engine}:{database}:{namespace}",
                    name=namespace,
                    kind="schema",
                    path=[database, namespace],
                    selectable=True,
                    children=object_children,
                )
            )
        return [
            SourceObject(
                id=f"database:{engine}:{database}",
                name=database,
                kind="database",
                path=[database],
                selectable=True,
                children=children,
            )
        ]

    async def list_sources(self) -> list[AnalyzerSource]:
        built_in: list[AnalyzerSource] = []
        source_assets = [
            asset for asset in self._registry.assets if asset.ownership == "SOURCE_SYSTEM"
        ]
        for engine in ("MONGODB", "SQLSERVER"):
            databases = sorted(
                {asset.database for asset in source_assets if asset.engine == engine}
            )
            for database in databases:
                assets = [
                    asset
                    for asset in source_assets
                    if asset.engine == engine and asset.database == database
                ]
                source_id = f"configured:{engine.lower()}:{database}"
                built_in.append(
                    AnalyzerSource(
                        id=source_id,
                        name=f"{database} ({engine})",
                        engine=cast(Any, engine),
                        status="NOT_VALIDATED",
                        host=(
                            "Configured endpoint"
                            if engine == "MONGODB"
                            else self._settings.sqlserver_host
                        ),
                        port=(27017 if engine == "MONGODB" else self._settings.sqlserver_port),
                        database=database,
                        username=None,
                        lastValidatedAt=None,
                        objectCount=len(assets),
                        objects=self._source_objects(engine, database, assets),
                    )
                )
        documents = await self._sources.find({}).sort("name", 1).to_list(length=500)
        configured = [self._source_view(cast(dict[str, Any], document)) for document in documents]
        return [*built_in, *configured]

    @staticmethod
    def _source_view(document: dict[str, Any]) -> AnalyzerSource:
        return AnalyzerSource(
            id=str(document["_id"]),
            name=str(document["name"]),
            engine=document["engine"],
            status=document.get("status", "NOT_VALIDATED"),
            host=str(document["host"]),
            port=int(document["port"]),
            database=str(document["database"]),
            username=str(document["username"]) if document.get("username") else None,
            lastValidatedAt=document.get("lastValidatedAt"),
            # Whatever the last successful discovery cached. Re-discovering on
            # every list would turn opening the workspace into one metadata scan
            # per configured source; `POST /sources/{id}/metadata` is the
            # explicit refresh.
            objectCount=int(document.get("objectCount", 0)),
            objects=[SourceObject.model_validate(node) for node in document.get("objects", [])],
        )

    async def save_source(self, payload: SourceInput, source_id: str | None) -> AnalyzerSource:
        identifier = source_id or str(uuid.uuid4())
        existing = await self._sources.find_one({"_id": identifier}) if source_id else None
        document = {
            "_id": identifier,
            "name": payload.name,
            "engine": payload.engine,
            "host": payload.host,
            "port": payload.port,
            "database": payload.database,
            "username": payload.username,
            "password": payload.password.get_secret_value()
            if payload.password is not None
            else (existing or {}).get("password"),
            "status": "NOT_VALIDATED",
            "lastValidatedAt": None,
            "updatedAt": _now(),
        }
        if not document["password"]:
            raise ValueError("A password is required for a new source connection.")
        await self._sources.replace_one({"_id": identifier}, document, upsert=True)
        return self._source_view(document)

    async def delete_source(self, source_id: str) -> bool:
        result = await self._sources.delete_one({"_id": source_id})
        return result.deleted_count == 1

    async def validate_source(self, source_id: str) -> AnalyzerSource:
        """Probe the source and, when it answers, refresh its discovered structure.

        Validation and discovery are one action because they are one question to
        the operator: "is this connection usable". A source that reports
        CONNECTED and then shows nothing to expand is not usable, and that was
        the previous behaviour for every source added through the UI.
        """
        document = await self._sources.find_one({"_id": source_id})
        if document is None:
            raise KeyError(source_id)
        status, _message = await probe_source(cast(dict[str, Any], document))
        update: dict[str, Any] = {"status": status, "lastValidatedAt": _now()}
        if status == "CONNECTED":
            try:
                objects, truncated = await discover_objects(cast(dict[str, Any], document))
            except Exception:  # noqa: BLE001 - discovery failure is a source state
                logger.warning("graph_analyzer_discovery_failed source_id=%s", source_id)
                update["status"] = "VALIDATION_FAILED"
            else:
                update["objects"] = [node.model_dump(mode="python") for node in objects]
                update["objectCount"] = count_objects(objects)
                update["objectsTruncated"] = truncated
        updated = await self._sources.find_one_and_update(
            {"_id": source_id},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise KeyError(source_id)
        return self._source_view(cast(dict[str, Any], updated))

    async def test_source(
        self, payload: SourceInput, source_id: str | None = None
    ) -> tuple[str, str]:
        document = payload.model_dump(mode="python")
        password = payload.password.get_secret_value() if payload.password else ""
        if not password and source_id is not None:
            existing = await self._sources.find_one({"_id": source_id})
            if existing is None:
                raise KeyError(source_id)
            stored_password = existing.get("password")
            password = stored_password if isinstance(stored_password, str) else ""
        document["password"] = password
        document.setdefault("_id", source_id or "unsaved")
        return await probe_source(document)

    def existing_schema(self) -> AnalyzerGraphSchema:
        entities: list[GraphEntity] = []
        by_label: dict[str, str] = {}
        node_total = len(self._registry.graph.nodes)
        for index, node in enumerate(self._registry.graph.nodes):
            entity_id = f"existing:{node.label}"
            by_label[node.label] = entity_id
            source_asset = self._registry.asset(node.source_assets[0])
            entities.append(
                GraphEntity(
                    id=entity_id,
                    name=node.label,
                    description="Current system graph entity",
                    x=_canvas_position(index, node_total)[0],
                    y=_canvas_position(index, node_total)[1],
                    properties=[
                        GraphProperty(
                            id=f"{entity_id}:{name}",
                            name=name,
                            dataType=next(
                                (field.type for field in source_asset.fields if field.name == name),
                                "string",
                            ),
                            required=name == node.key_property,
                            identifier=name == node.key_property,
                            indexed=name == node.key_property,
                            sourceObjectId=source_asset.asset_id,
                            sourceField=name,
                        )
                        for name in node.properties
                    ],
                    constraints=[f"UNIQUE({node.key_property})"],
                    change="UNCHANGED",
                )
            )
        relationships = [
            GraphRelationship(
                id=f"existing:{item.type}",
                name=item.type,
                fromEntityId=by_label[item.from_label],
                toEntityId=by_label[item.to_label],
                direction="OUTBOUND",
                properties=[],
                sourceObjectId=None,
                change="UNCHANGED",
            )
            for item in self._registry.graph.relationships
        ]
        return AnalyzerGraphSchema(
            id="existing-system-graph",
            version=1,
            status="FINALIZED",
            updatedAt=_now(),
            entities=entities,
            relationships=relationships,
        )

    async def proposed_schema(self) -> AnalyzerGraphSchema | None:
        document = await self._schemas.find_one({"kind": "proposed"}, sort=[("version", -1)])
        return AnalyzerGraphSchema.model_validate(document["schema"]) if document else None

    async def _resolve_selection(
        self, selected: Sequence[str]
    ) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]], list[str]]:
        """Split a selection into configured-source objects and unresolvable ids.

        Container nodes -- a database or namespace row -- select their whole
        subtree in the UI, so their own ids arrive here too and are not objects.
        They resolve to nothing and are not reported as unavailable.
        """
        documents: dict[str, dict[str, Any]] = {}
        pairs: list[tuple[str, str]] = []
        unresolved: list[str] = []
        for object_id in dict.fromkeys(selected):
            source_id, _, object_name = object_id.partition(":")
            if not object_name or object_name.startswith(("namespace:", "database:")):
                continue
            if source_id not in documents:
                document = await self._sources.find_one({"_id": source_id})
                if document is None:
                    unresolved.append(object_id)
                    continue
                documents[source_id] = cast(dict[str, Any], document)
            pairs.append((source_id, object_name))
        return documents, pairs, unresolved

    async def start_analysis(self, request: AnalysisRequest) -> AnalysisRun:
        """Analyze exactly the selected scope and store the resulting proposal.

        Only what the operator selected is read. A selection that resolves to no
        readable object is refused with a message rather than answered with a
        proposal built from something else, which is what the previous
        implementation did when it fell back to the platform's own registry.
        """
        run_id = str(uuid.uuid4())
        started = _now()
        documents, pairs, unresolved = await self._resolve_selection(request.selectedObjectIds)

        registry_assets = [
            asset
            for asset in self._registry.assets
            if asset.ownership == "SOURCE_SYSTEM"
            and asset.asset_id in set(request.selectedObjectIds)
        ]

        if len(pairs) + len(registry_assets) > MAX_OBJECTS_PER_ANALYSIS:
            raise ValueError(
                f"Select at most {MAX_OBJECTS_PER_ANALYSIS} objects for one analysis; "
                f"{len(pairs) + len(registry_assets)} are selected."
            )
        if not pairs and not registry_assets:
            raise ValueError(
                "No selected source object could be read. Refresh the source metadata "
                "or revalidate the connection, then select objects to analyze."
            )

        evidence = await gather_evidence(documents, pairs)
        evidence.extend(self._registry_evidence(registry_assets))
        if not evidence:
            raise ValueError(
                "The selected objects could not be described. The source may have "
                "become unreachable since it was last validated."
            )

        stage: str = "COMPLETE"
        entities: list[GraphEntity]
        relationships: list[GraphRelationship]
        if self._reasoning is not None:
            try:
                proposal = await reasoned_proposal(
                    self._reasoning,
                    analysis_id=run_id,
                    evidence=evidence,
                    context=request.context,
                )
                entities, relationships = ground_proposal(proposal, evidence)
            except Exception:  # noqa: BLE001 - a model outage must not lose the analysis
                logger.warning("graph_analyzer_reasoning_failed run_id=%s", run_id, exc_info=True)
                entities, relationships = deterministic_proposal(evidence)
                stage = "COMPLETE_WITHOUT_MODEL"
            else:
                if not entities:
                    entities, relationships = deterministic_proposal(evidence)
                    stage = "COMPLETE_WITHOUT_MODEL"
        else:
            entities, relationships = deterministic_proposal(evidence)
            stage = "COMPLETE_WITHOUT_MODEL"

        # Positions are assigned once, here, so the canvas is laid out the same
        # way whichever path produced the proposal.
        placed = [
            entity.model_copy(
                update=dict(
                    zip(
                        ("x", "y"),
                        _canvas_position(index, len(entities)),
                        strict=True,
                    )
                )
            )
            for index, entity in enumerate(entities)
        ]

        schema = AnalyzerGraphSchema(
            id=str(uuid.uuid4()),
            version=(await self._next_schema_version()),
            status="VALIDATION_REQUIRED",
            updatedAt=_now(),
            entities=placed,
            relationships=relationships,
        )
        await self._schemas.insert_one(
            {
                "kind": "proposed",
                "version": schema.version,
                "schema": schema.model_dump(mode="python"),
                "analysisContext": request.context,
            }
        )
        run = AnalysisRun(
            id=run_id,
            status="COMPLETED",
            stage=cast(Any, stage),
            selectedObjectIds=list(request.selectedObjectIds),
            startedAt=started,
            completedAt=_now(),
            warningCount=len(unresolved),
        )
        await self._analyses.insert_one(run.model_dump(mode="python") | {"_id": run.id})
        return run

    def _registry_evidence(self, assets: Sequence[Any]) -> list[ObjectEvidence]:
        """Evidence for the platform's own registry-declared sources.

        They are configuration rather than a live connection, so their metadata
        is read from the registry instead of a connector -- but they enter the
        same proposal path as everything else.
        """
        return [
            ObjectEvidence(
                object_id=asset.asset_id,
                source_id=f"configured:{asset.engine.lower()}:{asset.database}",
                source_name=f"{asset.database} ({asset.engine})",
                engine=asset.engine,
                object_name=asset.name,
                fields=tuple(
                    {"name": item.name, "type": item.type, "nullable": not item.required}
                    for item in asset.fields
                ),
                identifier_fields=tuple(item.name for item in asset.fields if item.key),
                indexed_fields=tuple(item.name for item in asset.fields if item.key),
                relationships=(),
                approximate_rows=None,
            )
            for asset in assets
        ]

    async def _next_schema_version(self) -> int:
        document = await self._schemas.find_one({}, sort=[("version", -1)])
        if document is None:
            return 1
        version = document.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise RuntimeError("Stored analyzer schema version is invalid.")
        return version + 1

    async def get_analysis(self, run_id: str) -> AnalysisRun | None:
        document = await self._analyses.find_one({"_id": run_id})
        return AnalysisRun.model_validate(document) if document else None

    async def save_schema(self, schema: AnalyzerGraphSchema) -> AnalyzerGraphSchema:
        updated = schema.model_copy(
            update={
                "status": "VALIDATION_REQUIRED",
                "version": schema.version + 1,
                "updatedAt": _now(),
            }
        )
        await self._schemas.insert_one(
            {
                "kind": "proposed",
                "version": updated.version,
                "schema": updated.model_dump(mode="python"),
            }
        )
        return updated

    async def validate_schema(self) -> SchemaValidation:
        schema = await self.proposed_schema()
        if schema is None:
            raise ValueError("No proposed graph schema exists.")
        known_assets = {asset.asset_id: asset for asset in self._registry.assets}
        issues: list[ValidationIssue] = []
        entity_ids = {entity.id for entity in schema.entities}
        for entity in schema.entities:
            identifiers = [prop for prop in entity.properties if prop.identifier]
            if len(identifiers) != 1:
                issues.append(
                    ValidationIssue(
                        id=str(uuid.uuid4()),
                        severity="BLOCKING",
                        code="IDENTIFIER_COUNT",
                        message="Each graph entity must have exactly one identifier.",
                        objectId=entity.id,
                    )
                )
            for prop in entity.properties:
                asset = known_assets.get(prop.sourceObjectId or "")
                if asset is None or prop.sourceField not in {field.name for field in asset.fields}:
                    issues.append(
                        ValidationIssue(
                            id=str(uuid.uuid4()),
                            severity="BLOCKING",
                            code="STALE_SOURCE_MAPPING",
                            message=(
                                f"{entity.name}.{prop.name} references unavailable source metadata."
                            ),
                            objectId=prop.id,
                        )
                    )
        for relationship in schema.relationships:
            if (
                relationship.fromEntityId not in entity_ids
                or relationship.toEntityId not in entity_ids
            ):
                issues.append(
                    ValidationIssue(
                        id=str(uuid.uuid4()),
                        severity="BLOCKING",
                        code="INVALID_ENDPOINT",
                        message=f"{relationship.name} has an invalid endpoint.",
                        objectId=relationship.id,
                    )
                )
        status = (
            "BLOCKING"
            if any(issue.severity == "BLOCKING" for issue in issues)
            else "WARNING"
            if issues
            else "VALID"
        )
        validation = SchemaValidation(status=status, checkedAt=_now(), issues=issues)
        await self._validations.insert_one(
            validation.model_dump(mode="python") | {"schemaId": schema.id}
        )
        if status != "BLOCKING":
            ready = schema.model_copy(update={"status": "READY", "updatedAt": _now()})
            await self._schemas.insert_one(
                {
                    "kind": "proposed",
                    "version": ready.version + 1,
                    "schema": ready.model_copy(update={"version": ready.version + 1}).model_dump(
                        mode="python"
                    ),
                }
            )
        return validation

    async def latest_validation(self) -> SchemaValidation | None:
        document = await self._validations.find_one({}, sort=[("checkedAt", -1)])
        return SchemaValidation.model_validate(document) if document else None

    async def finalize_schema(self) -> AnalyzerGraphSchema:
        validation = await self.validate_schema()
        if validation.status == "BLOCKING":
            raise ValueError(
                "Blocking schema validation issues must be resolved before finalization."
            )
        schema = await self.proposed_schema()
        if schema is None:
            raise ValueError("No proposed graph schema exists.")
        finalized = schema.model_copy(
            update={"status": "FINALIZED", "version": schema.version + 1, "updatedAt": _now()}
        )
        await self._schemas.insert_one(
            {
                "kind": "proposed",
                "version": finalized.version,
                "schema": finalized.model_dump(mode="python"),
            }
        )
        return finalized

    async def ask_agent(self, request: AgentRequest) -> AgentReply:
        """Answer an operator's question about the active workspace.

        This used to be a formatted string with a keyword match on "index": it
        never called a model, so the same sentence came back for every question
        and the only "recommendation" it could produce was one it had written
        itself. It now runs on the shared route pool, through the same
        interception and failover as every other AI call on the platform.

        Everything that came out of a source -- object names, field names,
        sampled values -- and the operator's own message reach the model inside
        the six-block untrusted framing, so source content cannot address the
        model as policy.
        """
        if self._agent is None:
            raise AgentUnavailableError(
                "The Analyzer Agent is unavailable because no AI provider "
                "credential is configured for this deployment."
            )

        proposed = await self.proposed_schema()
        context_blocks = await self._agent_context_blocks(request, proposed)
        answer = await self._agent.answer(
            conversation_id=str(uuid.uuid4()), prompt_blocks=context_blocks
        )

        recommendation: AgentRecommendation | None = None
        if answer.operations:
            for operation in answer.operations:
                # Belt and braces over a closed type: the port cannot express a
                # source target, and this refuses one anyway before the
                # recommendation is ever persisted.
                assert_system_graph_target(operation.target)
            recommendation = AgentRecommendation(
                id=str(uuid.uuid4()),
                summary=answer.summary or "Proposed system graph change",
                rationale=answer.rationale or answer.message,
                status="PENDING",
                operations=[operation.model_dump(mode="python") for operation in answer.operations],
            )
            await self._recommendations.insert_one(
                recommendation.model_dump(mode="python") | {"_id": recommendation.id}
            )
        return AgentReply(
            message=AgentMessage(
                id=str(uuid.uuid4()), role="AGENT", content=answer.message, createdAt=_now()
            ),
            recommendation=recommendation,
        )

    async def _agent_context_blocks(
        self, request: AgentRequest, proposed: AnalyzerGraphSchema | None
    ) -> list[dict[str, Any]]:
        """Frame the active workspace for the model as untrusted evidence.

        The operator should not have to restate what they already have selected,
        so the current source, object, graph object and sync scope travel with
        every turn.
        """
        selected_object = request.context.selectedObjectId
        metadata: list[dict[str, Any]] = [
            {
                "workspace": request.context.workspace,
                "selected_source": request.context.selectedSourceId,
                "selected_object": selected_object,
                "selected_graph_object": request.context.selectedGraphObjectId,
                "selected_scope_size": len(request.context.selectedScope or []),
                "sync_run": request.context.syncRunId,
            }
        ]
        if proposed is not None:
            metadata.append(
                {
                    "proposed_schema_version": proposed.version,
                    "proposed_status": proposed.status,
                    "entities": [
                        {
                            "id": entity.id,
                            "name": neutralize_delimiters(entity.name),
                            "identifiers": [
                                neutralize_delimiters(prop.name)
                                for prop in entity.properties
                                if prop.identifier
                            ],
                            "indexed": [
                                neutralize_delimiters(prop.name)
                                for prop in entity.properties
                                if prop.indexed
                            ],
                            "properties": [
                                neutralize_delimiters(prop.name) for prop in entity.properties
                            ],
                            "source_object": (
                                entity.properties[0].sourceObjectId if entity.properties else None
                            ),
                        }
                        for entity in proposed.entities
                    ],
                    "relationships": [
                        {
                            "id": relationship.id,
                            "type": neutralize_delimiters(relationship.name),
                            "from": relationship.fromEntityId,
                            "to": relationship.toEntityId,
                            "direction": relationship.direction,
                        }
                        for relationship in proposed.relationships
                    ],
                }
            )
        if selected_object is not None:
            resolved = await self._source_document_for(selected_object)
            if resolved is not None:
                document, object_name = resolved
                evidence = await gather_evidence(
                    {str(document["_id"]): document}, [(str(document["_id"]), object_name)]
                )
                metadata.extend(
                    {
                        "dataset": neutralize_delimiters(item.object_name),
                        "source": neutralize_delimiters(item.source_name),
                        "engine": item.engine,
                        "approximate_rows": item.approximate_rows,
                        "fields": [
                            {
                                "name": neutralize_delimiters(str(field_item["name"])),
                                "type": neutralize_delimiters(str(field_item["type"])),
                                "nullable": field_item["nullable"],
                            }
                            for field_item in item.fields
                        ],
                        "declared_relationships": item.relationships,
                    }
                    for item in evidence
                )
        blocks = build_prompt_blocks(
            task_definition=_AGENT_TASK_DEFINITION,
            source_metadata=metadata,
            untrusted_samples=None,
            user_requirements=request.message,
        )
        return [block.model_dump() for block in blocks]

    async def review_recommendation(
        self, recommendation_id: str, apply: bool
    ) -> RecommendationResult:
        document = await self._recommendations.find_one({"_id": recommendation_id})
        if document is None:
            raise KeyError(recommendation_id)
        recommendation = AgentRecommendation.model_validate(document)
        for operation in recommendation.operations:
            assert_system_graph_target(str(operation.get("target", "")))
        if recommendation.status != "PENDING":
            raise ValueError("The recommendation has already been reviewed.")
        proposed = await self.proposed_schema()
        if apply:
            if proposed is None:
                raise ValueError("No proposed graph schema exists.")
            for operation in recommendation.operations:
                if operation.get("type") != "ADD_SYSTEM_GRAPH_INDEX":
                    raise ValueError("Unsupported system graph recommendation operation.")
                object_id = str(operation.get("objectId", ""))
                changed = False
                entities: list[GraphEntity] = []
                for entity in proposed.entities:
                    applies_to_entity = entity.id == object_id
                    properties = [
                        prop.model_copy(
                            update={"indexed": True}
                            if (applies_to_entity and prop.identifier) or prop.id == object_id
                            else {}
                        )
                        for prop in entity.properties
                    ]
                    changed = changed or properties != entity.properties
                    entities.append(entity.model_copy(update={"properties": properties}))
                if not changed:
                    raise ValueError("Recommendation target no longer exists.")
                proposed = await self.save_schema(
                    proposed.model_copy(update={"entities": entities})
                )
        status = "APPLIED" if apply else "REJECTED"
        updated = recommendation.model_copy(update={"status": status})
        result = await self._recommendations.update_one(
            {"_id": recommendation_id, "status": "PENDING"},
            {"$set": {"status": status}},
        )
        if result.modified_count != 1:
            raise ValueError("The recommendation changed while it was being reviewed.")
        return RecommendationResult(recommendation=updated, proposedSchema=proposed)

    async def preview(self, object_id: str, page: int, page_size: int) -> PreviewPage:
        """A bounded, read-only page from one source object.

        Object ids from a configured source are `"{source_id}:{object_name}"`,
        which is how one call routes to the connector that owns it. Registry
        assets keep their bare id and their existing path, so the platform's own
        sources still preview after this change.

        The read goes through `SourceInspectionPort.sample`, which takes a limit
        and no query string. Paging is applied over the bounded read rather than
        by asking the source for an offset, because an offset scan deep into a
        large table is the uncontrolled full scan the connector rules forbid.
        """
        document = await self._source_document_for(object_id)
        if document is None:
            return await self._preview_registry_asset(object_id, page, page_size)

        source_document, object_name = document
        offset = (page - 1) * page_size
        # One bounded read that covers the requested page; `MAX_PREVIEW_ROWS`
        # caps how deep paging can go rather than letting page number drive an
        # unbounded scan.
        limit = min(offset + page_size, MAX_PREVIEW_ROWS)
        rows = await sample_object(source_document, object_name, limit)
        window = [
            cast(dict[str, Any], _json_value(row)) for row in rows[offset : offset + page_size]
        ]
        columns = sorted({key for row in window for key in row})
        graph = (
            graph_sample(window, object_name.rsplit(".", 1)[-1])
            if source_document["engine"] == "NEO4J"
            else None
        )
        return PreviewPage(
            columns=columns,
            rows=window,
            page=page,
            pageSize=page_size,
            total=None,
            graph=graph,
        )

    async def _source_document_for(self, object_id: str) -> tuple[dict[str, Any], str] | None:
        """Resolve a configured-source object id to its source document."""
        if ":" not in object_id:
            return None
        source_id, _, object_name = object_id.partition(":")
        if not object_name or object_name.startswith(("namespace:", "database:")):
            return None
        document = await self._sources.find_one({"_id": source_id})
        if document is None:
            return None
        return cast(dict[str, Any], document), object_name

    async def _preview_registry_asset(
        self, object_id: str, page: int, page_size: int
    ) -> PreviewPage:
        """The platform's own registry-declared sources, unchanged."""
        asset = self._registry.asset(object_id)
        offset = (page - 1) * page_size
        if asset.engine == "MONGODB":
            database = self._source_client[asset.database]
            documents = (
                await database[asset.name]
                .find({})
                .skip(offset)
                .limit(page_size)
                .to_list(length=page_size)
            )
            rows = [cast(dict[str, Any], _json_value(document)) for document in documents]
        else:
            if (
                asset.namespace is None
                or not _SAFE_IDENTIFIER.fullmatch(asset.namespace)
                or not _SAFE_IDENTIFIER.fullmatch(asset.name)
            ):
                raise ValueError("Unsafe SQL Server source identifier.")
            query = (
                f"SELECT * FROM [{asset.namespace}].[{asset.name}] "
                "ORDER BY (SELECT NULL) OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
            )

            def read_sql() -> list[dict[str, Any]]:
                with pymssql.connect(
                    server=self._settings.sqlserver_host,
                    port=str(self._settings.sqlserver_port),
                    user=self._settings.sqlserver_user,
                    password=self._settings.sqlserver_password.get_secret_value(),
                    database=self._settings.sqlserver_database,
                    as_dict=True,
                    login_timeout=5,
                    timeout=5,
                ) as connection:
                    with connection.cursor(as_dict=True) as cursor:
                        cursor.execute(query, (offset, page_size))
                        return [
                            cast(dict[str, Any], _json_value(dict(row)))
                            for row in cursor.fetchall()
                        ]

            rows = await asyncio.to_thread(read_sql)
        columns = sorted({key for row in rows for key in row})
        return PreviewPage(
            columns=columns, rows=rows, page=page, pageSize=page_size, total=None, graph=None
        )

    async def _apply_system_graph_schema(self, schema: AnalyzerGraphSchema) -> None:
        async with self._graph_driver.session(
            database=self._settings.neo4j_database,
            default_access_mode=WRITE_ACCESS,
        ) as session:
            for entity in schema.entities:
                identifier = next((prop for prop in entity.properties if prop.identifier), None)
                if identifier is None:
                    # No identifier means no uniqueness constraint to create.
                    # A bare `next()` raised StopIteration here, which aborted
                    # the entire schema apply -- and therefore the sync -- on the
                    # first such entity. Validation is where a missing identifier
                    # is reported; it is not this function's job to crash over it.
                    continue
                constraint_name = f"gsa_uq_{entity.name.lower()}_{identifier.name.lower()}"
                for value in (entity.name, identifier.name, constraint_name):
                    if not _SAFE_IDENTIFIER.fullmatch(value):
                        raise ValueError("Unsafe system graph schema identifier.")
                result = await session.run(
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{entity.name}) REQUIRE n.{identifier.name} IS UNIQUE"
                )
                await result.consume()
                for prop in entity.properties:
                    if not prop.indexed or prop.identifier:
                        continue
                    index_name = f"gsa_ix_{entity.name.lower()}_{prop.name.lower()}"
                    if not _SAFE_IDENTIFIER.fullmatch(prop.name) or not _SAFE_IDENTIFIER.fullmatch(
                        index_name
                    ):
                        raise ValueError("Unsafe system graph index identifier.")
                    result = await session.run(
                        f"CREATE INDEX {index_name} IF NOT EXISTS "
                        f"FOR (n:{entity.name}) ON (n.{prop.name})"
                    )
                    await result.consume()

    async def start_sync(self, request: SyncRequest) -> SyncRun:
        schema = await self.proposed_schema()
        if schema is None or schema.status != "FINALIZED":
            raise ValueError("A finalized graph schema is required before synchronization.")
        scope = set(request.scope)
        entities = [
            entity
            for entity in schema.entities
            if request.mode == "FULL"
            or any(prop.sourceObjectId in scope for prop in entity.properties)
        ]
        run = SyncRun(
            id=str(uuid.uuid4()),
            mode=request.mode,
            status="RUNNING",
            scope=request.scope,
            currentSource=None,
            currentObject=None,
            currentActivity="Reading finalized source mappings",
            itemsRead=0,
            itemsProcessed=0,
            nodesWritten=0,
            relationshipsWritten=0,
            failedItems=0,
            startedAt=_now(),
            completedAt=None,
            error=None,
        )
        await self._sync_runs.insert_one(run.model_dump(mode="python") | {"_id": run.id})
        try:
            await self._apply_system_graph_schema(schema)
            read = processed = written = relationship_writes = skipped = 0
            for entity in entities:
                identifier = next((prop for prop in entity.properties if prop.identifier), None)
                if identifier is None or identifier.sourceObjectId is None:
                    # Nothing to key the graph node on, so this entity is skipped
                    # and counted rather than taking the run down with it.
                    skipped += 1
                    continue
                preview = await self.preview(
                    identifier.sourceObjectId, 1, min(self._settings.graph_sync_max_records, 10_000)
                )
                read += len(preview.rows)
                label = entity.name
                if not _SAFE_IDENTIFIER.fullmatch(label):
                    raise ValueError("Unsafe system graph label.")
                mapped = [
                    (prop.name, prop.sourceField) for prop in entity.properties if prop.sourceField
                ]
                rows = [{name: row.get(source) for name, source in mapped} for row in preview.rows]
                query = (
                    f"UNWIND $rows AS row MERGE (n:{label} "
                    f"{{{identifier.name}: row.{identifier.name}}}) SET n += row"
                )
                async with self._graph_driver.session(
                    database=self._settings.neo4j_database, default_access_mode=WRITE_ACCESS
                ) as session:
                    result = await session.run(query, rows=rows)
                    await result.consume()
                processed += len(rows)
                written += len(rows)
            entities_by_id = {entity.id: entity for entity in schema.entities}
            for relationship in schema.relationships:
                if relationship.sourceObjectId is None:
                    continue
                if request.mode == "PARTIAL" and relationship.sourceObjectId not in scope:
                    continue
                from_entity = entities_by_id[relationship.fromEntityId]
                to_entity = entities_by_id[relationship.toEntityId]
                # Same StopIteration hazard as the entity loop above: a
                # relationship whose endpoint has no identifier is skipped, not
                # allowed to abort a run that has already written nodes.
                from_identifier = next(
                    (prop for prop in from_entity.properties if prop.identifier), None
                )
                to_identifier = next(
                    (prop for prop in to_entity.properties if prop.identifier), None
                )
                if from_identifier is None or to_identifier is None:
                    skipped += 1
                    continue
                if from_identifier.sourceField is None or to_identifier.sourceField is None:
                    skipped += 1
                    continue
                preview = await self.preview(
                    relationship.sourceObjectId,
                    1,
                    min(self._settings.graph_sync_max_records, 10_000),
                )
                relationship_rows = [
                    {
                        "from_key": row.get(from_identifier.sourceField),
                        "to_key": row.get(to_identifier.sourceField),
                    }
                    for row in preview.rows
                    if row.get(from_identifier.sourceField) is not None
                    and row.get(to_identifier.sourceField) is not None
                ]
                for value in (
                    from_entity.name,
                    to_entity.name,
                    from_identifier.name,
                    to_identifier.name,
                    relationship.name,
                ):
                    if not _SAFE_IDENTIFIER.fullmatch(value):
                        raise ValueError("Unsafe system graph relationship identifier.")
                left, right = (
                    (from_entity, to_entity)
                    if relationship.direction == "OUTBOUND"
                    else (to_entity, from_entity)
                )
                left_key, right_key = (
                    (from_identifier, to_identifier)
                    if relationship.direction == "OUTBOUND"
                    else (to_identifier, from_identifier)
                )
                query = (
                    f"UNWIND $rows AS row MATCH (a:{left.name} "
                    f"{{{left_key.name}: row.from_key}}) MATCH (b:{right.name} "
                    f"{{{right_key.name}: row.to_key}}) "
                    f"MERGE (a)-[r:{relationship.name}]->(b) RETURN count(r) AS writes"
                )
                async with self._graph_driver.session(
                    database=self._settings.neo4j_database,
                    default_access_mode=WRITE_ACCESS,
                ) as session:
                    result = await session.run(query, rows=relationship_rows)
                    record = await result.single()
                    relationship_writes += int(record["writes"]) if record else 0
                read += len(preview.rows)
                processed += len(relationship_rows)
            # A run that skipped part of its scope did not fully complete, and
            # saying COMPLETED would hide that some entities were never written.
            completed = run.model_copy(
                update={
                    "status": "PARTIALLY_COMPLETED" if skipped else "COMPLETED",
                    "currentActivity": (
                        f"Complete; {skipped} object(s) skipped for a missing identifier"
                        if skipped
                        else "Synchronization complete"
                    ),
                    "itemsRead": read,
                    "itemsProcessed": processed,
                    "nodesWritten": written,
                    "relationshipsWritten": relationship_writes,
                    "failedItems": skipped,
                    "completedAt": _now(),
                }
            )
            await self._sync_runs.update_one(
                {"_id": run.id}, {"$set": completed.model_dump(mode="python")}
            )
            return completed
        except Exception as error:
            failed = run.model_copy(
                update={
                    "status": "FAILED",
                    "currentActivity": "Synchronization failed",
                    "failedItems": 1,
                    "error": type(error).__name__,
                    "completedAt": _now(),
                }
            )
            await self._sync_runs.update_one(
                {"_id": run.id}, {"$set": failed.model_dump(mode="python")}
            )
            raise

    async def get_sync(self, run_id: str) -> SyncRun | None:
        document = await self._sync_runs.find_one({"_id": run_id})
        return SyncRun.model_validate(document) if document else None

    async def sync_history(self) -> list[SyncRun]:
        documents = (
            await self._sync_runs.find({}).sort("startedAt", -1).limit(100).to_list(length=100)
        )
        return [SyncRun.model_validate(document) for document in documents]

    async def bootstrap(self) -> AnalyzerBootstrap:
        history = await self.sync_history()
        analysis_document = await self._analyses.find_one({}, sort=[("startedAt", -1)])
        return AnalyzerBootstrap(
            sources=await self.list_sources(),
            existingSchema=self.existing_schema(),
            proposedSchema=await self.proposed_schema(),
            validation=await self.latest_validation(),
            activeAnalysis=AnalysisRun.model_validate(analysis_document)
            if analysis_document
            else None,
            activeSync=next(
                (run for run in history if run.status in {"PREPARING", "RUNNING"}), None
            ),
            syncHistory=history,
        )
