from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class IntegrationDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = True
    credential_ref: str | None = None
    retry_policy: Mapping[str, Any] | None = None
    module_id: str | None = None
    module_type: str | None = None
    schema_version: str | None = None
    configuration_version: str | None = None
    owner: str | None = None
    status: str | None = None
    dependencies: list[Any] | None = None
    payload: Mapping[str, Any] | None = None


class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    integrations: Mapping[str, IntegrationDefinition] = {}
