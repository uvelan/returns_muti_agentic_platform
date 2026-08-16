from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class AiConfig(BaseModel):
    """The manifest's view of `ai_gateway.yaml`, key for key.

    `extra="forbid"` means every top-level key the gateway configuration grows
    has to be added here as well, or the packaged file stops loading through the
    canonical application path. That is the point -- it is what stops a key being
    published that nothing downstream knows how to read -- but it does make this
    a second place to edit, so the ordering below follows the file's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    schemaVersion: str | None = None
    domain: str | None = None
    circuitBreaker: Mapping[str, Any] | None = None
    retry: Mapping[str, Any] | None = None
    rateLimits: Mapping[str, Any] | None = None
    providerLimits: Mapping[str, Any] | None = None
    #: Declared model context windows, validated in detail by
    #: `ai.routing.tasks.ModelContextEntry`. A model whose window is below a
    #: task's `maximumInputTokens` is not offered that task's routes.
    modelContexts: Sequence[Mapping[str, Any]] | None = None
    tasks: Mapping[str, Any] | None = None
    routes: Mapping[str, Any] | None = None
    providers: Mapping[str, Any] | None = None
    safety: Mapping[str, Any] | None = None
    interception: Mapping[str, Any] | None = None
