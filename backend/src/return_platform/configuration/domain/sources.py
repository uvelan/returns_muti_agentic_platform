from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class SourceConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    connector_type: str
    connection_reference: str | None = None
    database_reference: str | None = None
    collection: str | None = None
    access_mode: str | None = None
    record_identity_expression: str | None = None
    order_id_path: str | None = None
    customer_id_path: str | None = None
    line_path: str | None = None
    line_number_path: str | None = None
    schema_fingerprint_required: bool = True
    enabled: bool = True

class SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sources: Mapping[str, SourceConfigNode] = {}
