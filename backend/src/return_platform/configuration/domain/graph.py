from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class GraphSchemaNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_name: str | None = None
    configuration_release_id: str | None = None
    release_status: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    schema_version: str | None = None
    policy_version: str | None = None
    prompt_version: str | None = None
    compiler_version: str | None = None
    runtime_mode: str | None = None
    sources: Mapping[str, Any] | None = None
    entities: Mapping[str, Any] | None = None
    nodes: Mapping[str, Any] | None = None
    relationships: List[Any] | None = None
    constraints: List[str] | None = None
    projection_profiles: List[str] | None = None
    prohibit_raw_source_payload: bool = True

class GraphConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    graphs: Mapping[str, GraphSchemaNode] = {}
    settings: Mapping[str, Any] | None = None
