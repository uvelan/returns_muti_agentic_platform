"""Validated AI Gateway routing, safety, retry, and task configuration."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from return_platform.ai.pricing import AIPricingCatalog


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelTier(StrEnum):
    LIGHTWEIGHT = "LIGHTWEIGHT"
    STANDARD = "STANDARD"


class FallbackStrategy(StrEnum):
    TEMPLATE = "TEMPLATE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class CircuitBreakerConfiguration(StrictModel):
    failureThreshold: int = Field(default=3, ge=1, le=20)
    openSeconds: int = Field(default=60, ge=1, le=3600)
    authFailureOpenSeconds: int = Field(default=900, ge=30, le=86400)
    rateLimitCooldownSeconds: int = Field(default=60, ge=1, le=3600)


class RetryConfiguration(StrictModel):
    maximumAttemptsPerRoute: int = Field(default=1, ge=1, le=4)
    maximumTotalAttempts: int = Field(default=6, ge=1, le=20)
    initialBackoffMilliseconds: int = Field(default=200, ge=0, le=10000)
    maximumBackoffMilliseconds: int = Field(default=2000, ge=0, le=60000)
    jitter: bool = True

    @model_validator(mode="after")
    def validate_backoff(self) -> RetryConfiguration:
        if self.maximumBackoffMilliseconds < self.initialBackoffMilliseconds:
            raise ValueError("maximumBackoffMilliseconds must be >= initialBackoffMilliseconds")
        return self


class LimitConfiguration(StrictModel):
    requestsPerMinute: int = Field(ge=1, le=1_000_000)
    tokensPerMinute: int = Field(ge=1, le=1_000_000_000)
    maximumConcurrency: int | None = Field(default=None, ge=1, le=10_000)


class TierLimitConfiguration(LimitConfiguration):
    maximumConcurrency: int = Field(ge=1, le=10_000)


class RateLimitConfiguration(StrictModel):
    application: LimitConfiguration
    lightweight: TierLimitConfiguration
    standard: TierLimitConfiguration


class TaskConfiguration(StrictModel):
    tier: ModelTier
    promptVersion: str = Field(min_length=1, max_length=128)
    #: A budget, not a provider limit, and it is worth saying which.
    #:
    #: Nothing rejects a prompt at 12,000 characters: what a request is actually
    #: bounded by is `maximumInputTokens` below, and the largest prompt here --
    #: `ORDER_AGENT_REASONING_V1` -- assembles to roughly 22,200 characters once
    #: the response schema and the temporal addendum are appended, inside a turn
    #: that spends about 16,750 of its 32,000-token allowance, most of it
    #: `contextJson`. The cap exists so a prompt cannot grow without anyone
    #: noticing, and so a task with a small `maximumInputTokens` (1,000, for
    #: `RETURN_DISCOVERY_INTENT_V1`) cannot be handed a prompt that alone exceeds
    #: it. Raised from 12,000 when Order Discovery's progressive-narrowing and
    #: aggregation rules were written: the prompt they went into had 67
    #: characters of headroom, and the alternative to raising it was dropping
    #: rules to fit a number no provider enforces.
    systemPrompt: str = Field(min_length=20, max_length=14_000)
    fallbackStrategy: FallbackStrategy
    fallbackTemplate: str = Field(min_length=1, max_length=128)
    maximumOutputTokens: int = Field(ge=32, le=8192)
    maximumInputTokens: int = Field(ge=256, le=200_000)
    allowTierEscalation: bool = False
    allowedProviders: tuple[
        Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR", "MANUAL"], ...
    ] = Field(min_length=1)
    allowedInputKeys: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_values(self) -> TaskConfiguration:
        if len(set(self.allowedProviders)) != len(self.allowedProviders):
            raise ValueError("allowedProviders must be unique")
        if len(set(self.allowedInputKeys)) != len(self.allowedInputKeys):
            raise ValueError("allowedInputKeys must be unique")
        return self


class AIGatewayConfiguration(StrictModel):
    schemaVersion: str = Field(min_length=1, max_length=32)
    domain: Literal["FERGUSON_RETURN_OPERATIONS"]
    circuitBreaker: CircuitBreakerConfiguration
    retry: RetryConfiguration
    rateLimits: RateLimitConfiguration
    providerLimits: dict[str, LimitConfiguration]
    tasks: dict[str, TaskConfiguration]
    # W4.11. Prices belong to the AI domain and change on their own schedule, so
    # they ride the release mechanism every other runtime change already uses:
    # declared here, validated on save, published as an immutable checksummed
    # release. Nothing writes a rate into packaged YAML.
    #
    # Defaulted to empty rather than required so that every release published
    # before this field existed still validates -- and, more importantly, still
    # validates to *no prices*, which reports UNKNOWN. Defaulting to a shipped
    # price list would be worse than the hardcoded zero it replaces: it would be
    # confidently wrong instead of obviously absent.
    pricing: AIPricingCatalog = AIPricingCatalog()

    @model_validator(mode="after")
    def validate_registry(self) -> AIGatewayConfiguration:
        required_tasks = {"RETURN_ELIGIBILITY_V1", "SIMULATOR_OPERATION_NARRATIVE_V1"}
        missing = required_tasks - set(self.tasks)
        if missing:
            raise ValueError(f"AI task registry is missing: {', '.join(sorted(missing))}")
        allowed_providers = {
            "GOOGLE",
            "NVIDIA",
            "OPENAI",
            "ANTHROPIC",
            "OLLAMA",
            "SIMULATOR",
            "MANUAL",
        }
        unknown = set(self.providerLimits) - allowed_providers
        if unknown:
            raise ValueError(f"Unknown provider limit entries: {', '.join(sorted(unknown))}")
        return self


class LoadedAIGatewayConfiguration(StrictModel):
    path: Path
    sha256: str
    configuration: AIGatewayConfiguration


def load_ai_gateway_configuration(path: Path) -> LoadedAIGatewayConfiguration:
    resolved = path.expanduser().resolve(strict=True)
    raw = resolved.read_bytes()
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("AI Gateway configuration must be a YAML object")
    return LoadedAIGatewayConfiguration(
        path=resolved,
        sha256=hashlib.sha256(raw).hexdigest(),
        configuration=AIGatewayConfiguration.model_validate(payload),
    )


def build_loaded_ai_gateway_configuration(
    configuration: AIGatewayConfiguration,
    *,
    path: Path,
) -> LoadedAIGatewayConfiguration:
    """Build a digest-addressed loaded view from a validated graph payload."""

    encoded = json.dumps(
        configuration.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return LoadedAIGatewayConfiguration(
        path=path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        configuration=configuration,
    )
