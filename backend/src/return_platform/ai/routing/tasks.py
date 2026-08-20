"""Validated AI Gateway routing, safety, retry, and task configuration."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

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


#: How long one named prompt section may be.
#:
#: The number that matters day to day. A section is *one concern* -- the payload
#: contract, the identity-first narrowing rule, the voice -- and the whole point
#: of naming it is that it stays small enough to read in one sitting and to
#: review as a diff on its own. The twenty-one the Order Agent's prompt
#: decomposes into run from 324 to 1,218 characters, so 2,000 is room to grow a
#: concern rather than a wall to squeeze it against -- and a section that reaches
#: the ceiling is a section that wants splitting again, which is cheap, and the
#: reason this number is not larger.
#:
#: It is deliberately far below `SINGLE_PROMPT_MAX_CHARS`: no single section
#: should ever be able to become the monolith this decomposition took apart.
PROMPT_SECTION_MAX_CHARS = 2_000

#: How long a prompt written as one string may be.
#:
#: Unchanged at the value v16 raised it to, and it still governs every task but
#: the Order Agent's. The history is the argument for not raising it a third
#: time: 12,000, then 14,000, then 15,000, each rise paid for by a prompt that
#: had run out of room, and by the last one the largest prompt had 301
#: characters left and its own comments recorded rules added, rewritten and then
#: partly removed for space. Raising a single flat number is how a prompt grows
#: without anyone deciding that it should. `TaskConfiguration.prompt_budget` is
#: what a composed prompt is measured against instead.
SINGLE_PROMPT_MAX_CHARS = 15_000


class PromptSection(StrictModel):
    """One named, independently-maintained piece of a task's system prompt.

    The alternative this replaces is a single fourteen-thousand-character
    string. That string instructed a model on eight unrelated concerns at once,
    had no structure a reviewer could point at, and -- because the cap applies
    to the whole -- had reached the point where adding a rule meant deleting
    one. Its own YAML comments record rules added, rewritten, and then partly
    removed for space.

    A section is not a new runtime concept. `TaskConfiguration.systemPrompt` is
    still the one string every provider sees and every caller reads; sections
    are how that string is *written*, joined in declaration order by
    `TaskConfiguration._compose_system_prompt`. Nothing downstream of
    configuration loading knows they exist.

    `name` is for humans and for diffs -- it never reaches a model. Sections are
    an ordered tuple rather than a mapping because prompt order is meaning:
    the role and the untrusted-input framing have to come first, and a mapping
    would make that an accident of how someone typed the YAML.
    """

    #: Lowercase kebab-case, so a section name reads the same in YAML, in a diff
    #: and in a test that asserts one is present.
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    text: str = Field(min_length=20, max_length=PROMPT_SECTION_MAX_CHARS)


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
    #:
    #: The third rise is the one that did not happen. The bound now lives in
    #: `prompt_budget`, which is `SINGLE_PROMPT_MAX_CHARS` for a task that writes
    #: its prompt as one string -- the same 15,000, applied to every task but the
    #: Order Agent's -- and, for a task that writes it as named sections, those
    #: sections' own budgets. The `max_length` constraint is gone from the field
    #: itself because it could only express one of the two.
    systemPrompt: str = Field(min_length=20)
    #: The prompt written as named parts, joined in order into `systemPrompt`.
    #:
    #: Empty for every task whose prompt is short enough to read as one string,
    #: which is all of them but the Order Agent's. Declaring both a
    #: `systemPrompt` and sections that compose to something else is a config
    #: error rather than a precedence puzzle -- see `_compose_system_prompt`.
    systemPromptSections: tuple[PromptSection, ...] = ()
    fallbackStrategy: FallbackStrategy
    fallbackTemplate: str = Field(min_length=1, max_length=128)
    #: Optional, and omitting it is the way to send no ceiling at all.
    #:
    #: A cap is an optimisation, and it was costing more than it saved: Gemini
    #: 2.5 and later spend this same budget thinking before they answer, so a
    #: number chosen to bound the JSON truncated the reply mid-string and every
    #: turn failed as RESPONSE_INVALID. `ProviderRequest.max_output_tokens` has
    #: always been `int | None` and the Google adapter omits the key when it is
    #: None; only this field made it compulsory. A task that names no ceiling
    #: now gets the provider's own default, which is the model's real limit
    #: rather than a guess about it. Anthropic still requires one and supplies
    #: its own fallback.
    maximumOutputTokens: int | None = Field(default=None, ge=32, le=8192)
    maximumInputTokens: int = Field(ge=256, le=200_000)
    allowTierEscalation: bool = False
    allowedProviders: tuple[
        Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR", "MANUAL"], ...
    ] = Field(min_length=1)
    allowedInputKeys: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _compose_system_prompt(cls, data: Any) -> Any:
        """Join `systemPromptSections` into `systemPrompt`, in declaration order.

        Runs before field validation so that `systemPrompt` is present and
        already correct by the time its own constraints are checked -- which is
        what keeps the budget, the digest, the released payload and every reader
        of `task.systemPrompt` working on a composed task without knowing one.

        Sections are joined with a blank line rather than a space. The join is
        the only model-visible difference between a composed prompt and the
        single folded scalar it replaces, and a blank line between concerns is
        the half of "break it down" the model gets to see.

        A payload carrying *both* a `systemPrompt` and sections is normal: it is
        what `model_dump` produces, and therefore what a published release
        round-trips through. It is only rejected when the two disagree, because
        then someone has edited the composed copy and their edit is about to be
        silently discarded.
        """
        if not isinstance(data, dict):
            return data
        sections = data.get("systemPromptSections")
        if not sections:
            return data
        texts: list[str] = []
        for section in sections:
            if isinstance(section, PromptSection):
                texts.append(section.text)
            elif isinstance(section, dict) and isinstance(section.get("text"), str):
                texts.append(section["text"])
            else:
                # Malformed. Hand it back untouched so the field validator
                # reports the real shape error rather than this one.
                return data
        composed = "\n\n".join(text.strip() for text in texts)
        declared = data.get("systemPrompt")
        if isinstance(declared, str) and declared.strip() and declared != composed:
            raise ValueError(
                "systemPrompt and systemPromptSections disagree; the sections are "
                "the source of truth, so edit a section rather than the composed text"
            )
        return {**data, "systemPrompt": composed}

    @property
    def prompt_budget(self) -> int:
        """How long this task's assembled system prompt may be.

        Two shapes, two bounds, and the difference is the point.

        A prompt written as one string gets `SINGLE_PROMPT_MAX_CHARS`: a flat
        number, which is the right instrument for a prompt nobody has taken
        apart, and which stays where v16 left it.

        A prompt written as named sections is bounded by its sections' own
        budgets instead. That is deliberately not one number: what it means is
        that the prompt grows only by growing a named concern past
        `PROMPT_SECTION_MAX_CHARS`-worth of room, or by adding a concern -- and
        both of those are a line in a diff with a name on it. The flat cap could
        not express that, and its history is why it needed to: raised to 14,000,
        then 15,000, each time by a prompt that had run out of room, until the
        largest one had 301 characters left and was paying for new rules by
        deleting old ones.
        """
        if not self.systemPromptSections:
            return SINGLE_PROMPT_MAX_CHARS
        return len(self.systemPromptSections) * PROMPT_SECTION_MAX_CHARS

    @model_validator(mode="after")
    def validate_unique_values(self) -> TaskConfiguration:
        if len(set(self.allowedProviders)) != len(self.allowedProviders):
            raise ValueError("allowedProviders must be unique")
        if len(set(self.allowedInputKeys)) != len(self.allowedInputKeys):
            raise ValueError("allowedInputKeys must be unique")
        section_names = [section.name for section in self.systemPromptSections]
        if len(set(section_names)) != len(section_names):
            raise ValueError("systemPromptSections must have unique names")
        if len(self.systemPrompt) > self.prompt_budget:
            raise ValueError(
                f"systemPrompt is {len(self.systemPrompt)} characters, over this "
                f"task's {self.prompt_budget}-character budget"
            )
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
