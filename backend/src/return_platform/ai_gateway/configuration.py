"""Validated AI Gateway routing, safety, retry, and task configuration."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    systemPrompt: str = Field(min_length=20, max_length=12_000)
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
