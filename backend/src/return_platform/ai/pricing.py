"""What a model call cost, priced by the rate that was in force when it ran.

`estimatedCostMicrousd` was the integer `0` on every attempt the platform had
ever recorded. Zero is not "we do not know" -- it sums, it charts, and it makes
a quarter of model spend look free.

Two decisions define this module.

**Cost is computed at request time and stored.** The obvious alternative --
keep tokens, multiply by today's price when someone opens a dashboard -- rewrites
history every time a vendor changes a rate. A March invocation would silently
re-cost itself in June and no report would agree with the one printed the week
before.

**An unpriced call is `UNKNOWN`, never `0`.** A provider or model the catalog
does not cover produces `estimated_cost = None` and
`pricing_status = UNKNOWN`, so the gap is visible in the record rather than
absorbed into a total that looks complete.

Rates live in the AI gateway's released configuration (`AIGatewayConfiguration.
pricing`), which is checksum-verified and immutable per release, so "which
prices were in force" is answerable from the release id alone. Packaged YAML
stays seed material: an operator publishes a new price list the way every other
runtime change is made, by releasing it.

Money is integers throughout. `...PerMillionTokensMicros` is millionths of one
unit of `currency` per million tokens, which keeps a rate like $1.25/M exactly
representable (1_250_000) and every multiplication exact. Floats were never an
option for money and `Decimal` would have to survive a JSON round-trip through
the configuration graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AICostEstimate",
    "AIPricingCatalog",
    "AIPricingEntry",
    "AIPricingStatus",
]

_MICROS_PER_MILLION_TOKENS = 1_000_000

PricedProvider = Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR", "MANUAL"]


class AIPricingStatus(StrEnum):
    """Whether a recorded cost means anything.

    `UNKNOWN` exists so a missing price is a fact on the record rather than a
    zero in a sum. Anything that totals cost must exclude `UNKNOWN` rows and say
    how many it excluded.
    """

    PRICED = "PRICED"
    UNKNOWN = "UNKNOWN"


class AIPricingEntry(BaseModel):
    """One provider/model rate, in force from `effectiveFrom` until superseded.

    `version` is what a usage record stores, and it names *this row* rather than
    the catalog generation: one catalog legitimately holds several dated rows
    for the same model, and a catalog-level version would not say which of them
    priced a given call. The release id already versions the catalog as a whole.

    `source` is provenance -- where the number came from and when it was read.
    Without it a rate that turns out to be wrong cannot be traced to whoever
    entered it, and every correction is a guess.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64)
    provider: PricedProvider
    model: str = Field(min_length=1, max_length=128)
    effectiveFrom: datetime
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    inputPerMillionTokensMicros: int = Field(ge=0)
    cachedInputPerMillionTokensMicros: int = Field(ge=0)
    outputPerMillionTokensMicros: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_effective_from_is_absolute(self) -> AIPricingEntry:
        """A naive `effectiveFrom` is ambiguous by exactly one timezone offset,
        which is enough to select the wrong rate for calls near a price change."""
        if self.effectiveFrom.tzinfo is None:
            raise ValueError("effectiveFrom must carry a timezone offset")
        return self


@dataclass(frozen=True, slots=True)
class AICostEstimate:
    """What one invocation cost, or an explicit statement that nobody knows.

    `amount_micros` and `pricing_version` are both `None` exactly when `status`
    is `UNKNOWN`; there is no third state where a cost exists without the rate
    that produced it, because a number nobody can re-derive is not evidence.
    """

    status: AIPricingStatus
    amount_micros: int | None
    currency: str | None
    pricing_version: str | None

    @classmethod
    def unknown(cls) -> AICostEstimate:
        return cls(
            status=AIPricingStatus.UNKNOWN,
            amount_micros=None,
            currency=None,
            pricing_version=None,
        )


def _line_cost(tokens: int, per_million_micros: int) -> int:
    """Round half up, in integers. `round()` would round half to even, which is
    right for statistics and wrong for money."""
    if tokens <= 0 or per_million_micros <= 0:
        return 0
    numerator = tokens * per_million_micros
    return (numerator + _MICROS_PER_MILLION_TOKENS // 2) // _MICROS_PER_MILLION_TOKENS


class AIPricingCatalog(BaseModel):
    """Every rate the platform knows, as one released, immutable list.

    Empty by default and by design. A release that has never had prices entered
    prices nothing, reports `UNKNOWN`, and does not pretend the calls were free.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[AIPricingEntry, ...] = ()

    @model_validator(mode="after")
    def validate_unique_versions(self) -> AIPricingCatalog:
        """Two rows sharing a `version` would make a stored `pricing_version`
        ambiguous, which defeats the only reason the field exists."""
        versions = [entry.version for entry in self.entries]
        if len(set(versions)) != len(versions):
            raise ValueError("AI pricing entry versions must be unique")
        keys = [(entry.provider, entry.model, entry.effectiveFrom) for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "AI pricing has two entries for the same provider, model and effectiveFrom"
            )
        return self

    def effective(self, *, provider: str, model: str, at: datetime) -> AIPricingEntry | None:
        """The rate in force for this provider/model at `at`, or nothing.

        "In force" is the latest `effectiveFrom` at or before `at`. A rate dated
        in the future is deliberately not used yet: a price list can be
        published ahead of the day it takes effect, which is how a change lands
        without a deploy at midnight.
        """
        candidates = [
            entry
            for entry in self.entries
            if entry.provider == provider and entry.model == model and entry.effectiveFrom <= at
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda entry: entry.effectiveFrom)

    def estimate(
        self,
        *,
        provider: str | None,
        model: str | None,
        at: datetime,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> AICostEstimate:
        """Price one invocation, or say the price is unknown.

        `input_tokens` is the *uncached* prompt, and `cached_input_tokens` is
        billed separately at the cached rate; the two are added, not overlapped.
        Providers report the split differently -- some give a subset of the
        prompt count, some give a sibling -- so the adapter normalises it before
        it gets here, and this function never has to guess which convention it
        was handed.
        """
        if not provider or not model:
            return AICostEstimate.unknown()
        entry = self.effective(provider=provider, model=model, at=at)
        if entry is None:
            return AICostEstimate.unknown()
        amount = (
            _line_cost(input_tokens, entry.inputPerMillionTokensMicros)
            + _line_cost(cached_input_tokens, entry.cachedInputPerMillionTokensMicros)
            + _line_cost(output_tokens, entry.outputPerMillionTokensMicros)
        )
        return AICostEstimate(
            status=AIPricingStatus.PRICED,
            amount_micros=amount,
            currency=entry.currency,
            pricing_version=entry.version,
        )
