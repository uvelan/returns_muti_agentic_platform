from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AnalyzerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInput(AnalyzerModel):
    name: str = Field(min_length=1, max_length=100)
    engine: Literal["MONGODB", "POSTGRESQL", "SQLSERVER", "NEO4J"]
    host: str = Field(min_length=1, max_length=500)
    port: int = Field(ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr | None = None


class SourceField(AnalyzerModel):
    name: str
    dataType: str
    nullable: bool
    identifier: bool
    indexed: bool


class SourceObject(AnalyzerModel):
    id: str
    name: str
    kind: Literal["database", "schema", "table", "collection", "entity", "relationship", "field"]
    path: list[str]
    selectable: bool
    children: list[SourceObject] = Field(default_factory=list)
    fields: list[SourceField] | None = None
    estimatedRows: int | None = None


class AnalyzerSource(AnalyzerModel):
    id: str
    name: str
    engine: Literal["MONGODB", "POSTGRESQL", "SQLSERVER", "NEO4J"]
    status: Literal[
        "NOT_VALIDATED", "CONNECTED", "VALIDATION_FAILED", "AUTHENTICATION_FAILED", "UNREACHABLE"
    ]
    host: str
    #: Echoed back so the edit form can restore the connection it is editing.
    #: Never a secret; the password is the only field withheld.
    port: int = Field(ge=1, le=65_535)
    database: str
    username: str | None
    lastValidatedAt: datetime | None
    objectCount: int = Field(ge=0)
    objects: list[SourceObject]


class PreviewGraphNode(AnalyzerModel):
    id: str
    labels: list[str]
    properties: dict[str, Any]


class PreviewGraphEdge(AnalyzerModel):
    id: str
    type: str
    fromId: str
    toId: str
    properties: dict[str, Any]


class PreviewGraph(AnalyzerModel):
    nodes: list[PreviewGraphNode]
    edges: list[PreviewGraphEdge]


class PreviewPage(AnalyzerModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)
    total: int | None = Field(default=None, ge=0)
    #: Populated only for external graph sources, from a bounded read-only read.
    #: `None` elsewhere: the Graph tab renders an empty state rather than
    #: fabricating nodes, which is what it used to do.
    graph: PreviewGraph | None = None


class AnalysisRequest(AnalyzerModel):
    selectedObjectIds: list[str] = Field(min_length=1, max_length=10_000)
    context: str = Field(default="", max_length=12_000)


class AnalysisRun(AnalyzerModel):
    id: str
    status: Literal["RUNNING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED"]
    stage: Literal[
        "PREPARING",
        "READING_METADATA",
        "EVALUATING_IDENTIFIERS",
        "DISCOVERING_ENTITIES",
        "EVALUATING_RELATIONSHIPS",
        "BUILDING_PROPOSAL",
        "COMPARING_EXISTING",
        "REVIEWING_INDEXES",
        "VALIDATING",
        "COMPLETE",
        "FAILED",
    ]
    selectedObjectIds: list[str]
    startedAt: datetime
    completedAt: datetime | None
    warningCount: int = Field(ge=0)


class GraphProperty(AnalyzerModel):
    id: str
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    dataType: str
    required: bool
    identifier: bool
    indexed: bool
    sourceObjectId: str | None
    sourceField: str | None


class GraphEntity(AnalyzerModel):
    id: str
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    description: str = Field(max_length=1000)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    properties: list[GraphProperty]
    constraints: list[str]
    change: Literal["UNCHANGED", "ADDED", "CHANGED", "REMOVED"]


class GraphRelationship(AnalyzerModel):
    id: str
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    fromEntityId: str
    toEntityId: str
    direction: Literal["OUTBOUND", "INBOUND"]
    properties: list[GraphProperty]
    sourceObjectId: str | None
    change: Literal["UNCHANGED", "ADDED", "CHANGED", "REMOVED"]


class AnalyzerGraphSchema(AnalyzerModel):
    id: str
    version: int = Field(ge=1)
    status: Literal["DRAFT", "VALIDATION_REQUIRED", "READY", "FINALIZED"]
    updatedAt: datetime
    entities: list[GraphEntity]
    relationships: list[GraphRelationship]


class ValidationIssue(AnalyzerModel):
    id: str
    severity: Literal["WARNING", "BLOCKING"]
    code: str
    message: str
    objectId: str


class SchemaValidation(AnalyzerModel):
    status: Literal["VALID", "WARNING", "BLOCKING"]
    checkedAt: datetime
    issues: list[ValidationIssue]


class AgentContext(AnalyzerModel):
    workspace: Literal["ANALYZER", "SCHEMA", "SYNC"]
    selectedSourceId: str | None = None
    selectedObjectId: str | None = None
    selectedGraphObjectId: str | None = None
    selectedScope: list[str] | None = None
    syncRunId: str | None = None


class AgentRequest(AnalyzerModel):
    message: str = Field(min_length=1, max_length=4000)
    context: AgentContext

    @field_validator("message")
    @classmethod
    def reject_source_mutation_requests(cls, value: str) -> str:
        lowered = value.casefold()
        source_terms = ("source", "postgres", "sql server", "mongodb", "external neo4j")
        mutation_terms = (
            "insert",
            "update",
            "delete",
            "alter",
            "drop",
            "truncate",
            "create index",
            "create constraint",
        )
        if any(source in lowered for source in source_terms) and any(
            term in lowered for term in mutation_terms
        ):
            raise ValueError("The Analyzer Agent cannot propose or execute source mutations.")
        return value


class AgentMessage(AnalyzerModel):
    id: str
    role: Literal["USER", "AGENT"]
    content: str
    createdAt: datetime


class AgentRecommendation(AnalyzerModel):
    id: str
    summary: str
    rationale: str
    target: Literal["SYSTEM_GRAPH"] = "SYSTEM_GRAPH"
    status: Literal["PENDING", "APPLIED", "REJECTED"]
    operations: list[dict[str, Any]]


class AgentReply(AnalyzerModel):
    message: AgentMessage
    recommendation: AgentRecommendation | None


class RecommendationDecision(AnalyzerModel):
    decision: Literal["APPLY", "REJECT"]


class RecommendationResult(AnalyzerModel):
    recommendation: AgentRecommendation
    proposedSchema: AnalyzerGraphSchema | None


class SyncRequest(AnalyzerModel):
    mode: Literal["FULL", "PARTIAL"]
    scope: list[str] = Field(max_length=10_000)


class SyncRun(AnalyzerModel):
    id: str
    mode: Literal["FULL", "PARTIAL"]
    status: Literal[
        "PREPARING", "RUNNING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"
    ]
    scope: list[str]
    currentSource: str | None
    currentObject: str | None
    currentActivity: str
    itemsRead: int = Field(ge=0)
    itemsProcessed: int = Field(ge=0)
    nodesWritten: int = Field(ge=0)
    relationshipsWritten: int = Field(ge=0)
    failedItems: int = Field(ge=0)
    startedAt: datetime
    completedAt: datetime | None
    error: str | None


class AnalyzerBootstrap(AnalyzerModel):
    sources: list[AnalyzerSource]
    existingSchema: AnalyzerGraphSchema | None
    proposedSchema: AnalyzerGraphSchema | None
    validation: SchemaValidation | None
    activeAnalysis: AnalysisRun | None
    activeSync: SyncRun | None
    syncHistory: list[SyncRun]
