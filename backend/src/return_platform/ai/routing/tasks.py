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
    #: rules to fit a number no provider enforces. Raised again to 15,000 for
    #: v16's rule that a turn asking a question may not declare itself complete
    #: -- the defect it closes had already reached a live case, and the tripwire
    #: did its job by making the growth a decision rather than a side effect.
    systemPrompt: str = Field(min_length=20, max_length=15_000)
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


class ModelContextEntry(StrictModel):
    """How much a model can actually read, declared rather than discovered.

    `nvidia/nemotron-mini-4b-instruct` answered every `ORDER_AGENT_REASONING_V1`
    call with `HTTP 400 -- maximum context length is 4096 tokens, however you
    requested 24014`. That is not a transient provider failure and no amount of
    failover fixes it: the model cannot serve the task, it could never have
    served the task, and the platform paid a round trip per turn to be told so
    again. A model's context window is a fact about the model, it is knowable
    before the first call, and `TaskConfiguration.maximumInputTokens` is the
    number it has to be compared against.

    Declared in the released configuration rather than compiled in, for the
    reason `AIPricingCatalog` is: it is vendor fact that changes on the vendor's
    schedule, and a rate or a window baked into a wheel cannot be corrected
    without a deploy. `source` is the same provenance discipline -- a window that
    turns out to be wrong must be traceable to whoever wrote it down.

    **An undeclared model is not refused.** Absence means nobody has measured
    this model, which is not evidence that it is too small; refusing on silence
    would take every unlisted model out of service the moment this field was
    introduced. The stance matches pricing's `UNKNOWN`: the gap is visible and
    it does not masquerade as a finding.
    """

    provider: Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR", "MANUAL"]
    model: str = Field(min_length=1, max_length=128)
    #: Total tokens the model accepts across prompt and completion. Compared
    #: against `maximumInputTokens` alone, which is the *input* budget: a task
    #: whose input ceiling already exceeds the whole window cannot fit, and
    #: adding `maximumOutputTokens` to the comparison would additionally refuse
    #: routes that fit but leave little room, which is a tuning judgement and not
    #: an impossibility.
    maximumContextTokens: int = Field(ge=256, le=10_000_000)
    source: str = Field(min_length=1, max_length=512)


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
    #: Declared model context windows. Empty by default, so a release published
    #: before this field existed keeps every route it had.
    modelContexts: tuple[ModelContextEntry, ...] = ()

    def maximum_context_tokens(self, *, provider: str, model: str) -> int | None:
        """The declared window for this provider/model, or nothing if undeclared."""
        for entry in self.modelContexts:
            if entry.provider == provider and entry.model == model:
                return entry.maximumContextTokens
        return None

    def context_shortfall(
        self, *, provider: str, model: str, task: TaskConfiguration
    ) -> tuple[int, int] | None:
        """`(window, required)` when this model cannot serve this task, else nothing.

        The one place the comparison is written. `AIRoutePool` calls it twice --
        once at build time to say so in the log, once per selection to act on it
        -- and two copies of a rule that decides whether a request is even
        attempted would be two chances to disagree.
        """
        window = self.maximum_context_tokens(provider=provider, model=model)
        if window is None or window >= task.maximumInputTokens:
            return None
        return window, task.maximumInputTokens

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
        context_keys = [(entry.provider, entry.model) for entry in self.modelContexts]
        if len(set(context_keys)) != len(context_keys):
            raise ValueError("AI model context windows declare the same provider and model twice")
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
