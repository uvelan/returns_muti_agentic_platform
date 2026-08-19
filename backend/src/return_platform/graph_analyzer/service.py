from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pymssql
from neo4j import WRITE_ACCESS, AsyncDriver
from pymongo import AsyncMongoClient, ReturnDocument

from return_platform.configuration.settings import Settings
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry
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

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_name(value: str, fallback: str = "Entity") -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    candidate = "".join(word[:1].upper() + word[1:] for word in words) or fallback
    return candidate if candidate[0].isalpha() else f"{fallback}{candidate}"


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
    ) -> None:
        self._settings = settings
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
            database=str(document["database"]),
            username=str(document["username"]) if document.get("username") else None,
            lastValidatedAt=document.get("lastValidatedAt"),
            objectCount=0,
            objects=[],
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
        document = await self._sources.find_one({"_id": source_id})
        if document is None:
            raise KeyError(source_id)
        status = await self._probe(cast(dict[str, Any], document))
        updated = await self._sources.find_one_and_update(
            {"_id": source_id},
            {"$set": {"status": status, "lastValidatedAt": _now()}},
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
        status = await self._probe(document)
        message = (
            "Connection succeeded with read-only validation."
            if status == "CONNECTED"
            else "Connection could not be validated."
        )
        return status, message

    async def _probe(self, document: dict[str, Any]) -> str:
        engine = document["engine"]
        try:
            if engine == "MONGODB":
                from pymongo import AsyncMongoClient as Client

                uri = str(document["host"])
                if not uri.startswith(("mongodb://", "mongodb+srv://")):
                    uri = f"mongodb://{document['username']}:{document['password']}@{uri}:{document['port']}/{document['database']}"
                client: Client[dict[str, object]] = Client(uri, serverSelectionTimeoutMS=5_000)
                try:
                    await client[document["database"]].command("ping")
                finally:
                    await client.close()
            elif engine == "SQLSERVER":

                def sql_probe() -> None:
                    with pymssql.connect(
                        server=document["host"],
                        port=str(document["port"]),
                        user=document["username"],
                        password=document["password"],
                        database=document["database"],
                        login_timeout=5,
                        timeout=5,
                    ) as connection:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT 1")
                            cursor.fetchone()

                await asyncio.to_thread(sql_probe)
            elif engine == "NEO4J":
                from neo4j import READ_ACCESS, AsyncGraphDatabase

                driver = AsyncGraphDatabase.driver(
                    str(document["host"]), auth=(document["username"], document["password"])
                )
                try:
                    async with driver.session(
                        database=document["database"], default_access_mode=READ_ACCESS
                    ) as session:
                        result = await session.run("RETURN 1 AS ok")
                        await result.consume()
                finally:
                    await driver.close()
            else:
                return "UNREACHABLE"
        except Exception as error:
            name = type(error).__name__.casefold()
            return "AUTHENTICATION_FAILED" if "auth" in name or "login" in name else "UNREACHABLE"
        return "CONNECTED"

    def existing_schema(self) -> AnalyzerGraphSchema:
        entities: list[GraphEntity] = []
        by_label: dict[str, str] = {}
        for index, node in enumerate(self._registry.graph.nodes):
            entity_id = f"existing:{node.label}"
            by_label[node.label] = entity_id
            source_asset = self._registry.asset(node.source_assets[0])
            entities.append(
                GraphEntity(
                    id=entity_id,
                    name=node.label,
                    description="Current system graph entity",
                    x=20 + (index % 3) * 30,
                    y=25 + (index // 3) * 35,
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

    async def start_analysis(self, request: AnalysisRequest) -> AnalysisRun:
        known = {
            asset.asset_id: asset
            for asset in self._registry.assets
            if asset.ownership == "SOURCE_SYSTEM"
        }
        assets = [
            known[asset_id]
            for asset_id in dict.fromkeys(request.selectedObjectIds)
            if asset_id in known
        ]
        if not assets:
            raise ValueError("No selected source object has usable metadata.")
        run_id = str(uuid.uuid4())
        started = _now()
        entities: list[GraphEntity] = []
        for index, asset in enumerate(assets):
            entity_id = f"proposal:{asset.asset_id}"
            entities.append(
                GraphEntity(
                    id=entity_id,
                    name=_safe_name(asset.name),
                    description=asset.description,
                    x=18 + (index % 3) * 32,
                    y=22 + (index // 3) * 34,
                    properties=[
                        GraphProperty(
                            id=f"{entity_id}:{field.name}",
                            name=field.name,
                            dataType=field.type,
                            required=field.required,
                            identifier=field.key,
                            indexed=field.key,
                            sourceObjectId=asset.asset_id,
                            sourceField=field.name,
                        )
                        for field in asset.fields
                    ],
                    constraints=[
                        f"UNIQUE({next(field.name for field in asset.fields if field.key)})"
                    ],
                    change="ADDED",
                )
            )
        relationships: list[GraphRelationship] = []
        for target in entities:
            target_asset = known[target.properties[0].sourceObjectId or ""]
            target_fields = {field.name for field in target_asset.fields}
            for source in entities:
                if source.id == target.id:
                    continue
                source_key = next(
                    (item.sourceField for item in source.properties if item.identifier), None
                )
                target_key = next(
                    (item.sourceField for item in target.properties if item.identifier), None
                )
                if source_key and target_key and source_key in target_fields:
                    relationships.append(
                        GraphRelationship(
                            id=f"proposal:rel:{source.id}:{target.id}",
                            name=f"HAS_{target.name.upper()}",
                            fromEntityId=source.id,
                            toEntityId=target.id,
                            direction="OUTBOUND",
                            properties=[],
                            sourceObjectId=target_asset.asset_id,
                            change="ADDED",
                        )
                    )
                    break
        schema = AnalyzerGraphSchema(
            id=str(uuid.uuid4()),
            version=(await self._next_schema_version()),
            status="VALIDATION_REQUIRED",
            updatedAt=_now(),
            entities=entities,
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
            stage="COMPLETE",
            selectedObjectIds=list(request.selectedObjectIds),
            startedAt=started,
            completedAt=_now(),
            warningCount=len(request.selectedObjectIds) - len(assets),
        )
        await self._analyses.insert_one(run.model_dump(mode="python") | {"_id": run.id})
        return run

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
        selected = (
            request.context.selectedGraphObjectId
            or request.context.selectedObjectId
            or "the active selection"
        )
        content = (
            f"I reviewed {selected} in the {request.context.workspace.lower()} workspace. "
            "Source evidence is read-only; any approved change will be applied only to the "
            "proposed system graph."
        )
        recommendation: AgentRecommendation | None = None
        if "index" in request.message.casefold() and request.context.selectedGraphObjectId:
            recommendation = AgentRecommendation(
                id=str(uuid.uuid4()),
                summary="Add a system graph index to the selected identifier",
                rationale=(
                    "This can improve system graph lookups without changing any source index."
                ),
                status="PENDING",
                operations=[
                    {
                        "type": "ADD_SYSTEM_GRAPH_INDEX",
                        "objectId": request.context.selectedGraphObjectId,
                        "target": "SYSTEM_GRAPH",
                    }
                ],
            )
            await self._recommendations.insert_one(
                recommendation.model_dump(mode="python") | {"_id": recommendation.id}
            )
        return AgentReply(
            message=AgentMessage(
                id=str(uuid.uuid4()), role="AGENT", content=content, createdAt=_now()
            ),
            recommendation=recommendation,
        )

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
        return PreviewPage(columns=columns, rows=rows, page=page, pageSize=page_size, total=None)

    async def _apply_system_graph_schema(self, schema: AnalyzerGraphSchema) -> None:
        async with self._graph_driver.session(
            database=self._settings.neo4j_database,
            default_access_mode=WRITE_ACCESS,
        ) as session:
            for entity in schema.entities:
                identifier = next(prop for prop in entity.properties if prop.identifier)
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
            read = processed = written = relationship_writes = 0
            for entity in entities:
                identifier = next(prop for prop in entity.properties if prop.identifier)
                if identifier.sourceObjectId is None:
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
                from_identifier = next(prop for prop in from_entity.properties if prop.identifier)
                to_identifier = next(prop for prop in to_entity.properties if prop.identifier)
                if from_identifier.sourceField is None or to_identifier.sourceField is None:
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
            completed = run.model_copy(
                update={
                    "status": "COMPLETED",
                    "currentActivity": "Synchronization complete",
                    "itemsRead": read,
                    "itemsProcessed": processed,
                    "nodesWritten": written,
                    "relationshipsWritten": relationship_writes,
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
