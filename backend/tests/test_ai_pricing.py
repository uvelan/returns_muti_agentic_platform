"""W4.11: what a model call cost, and the refusal to guess when nobody knows.

The defect being closed is a single hardcoded `0`. The tests that matter are
therefore not the arithmetic ones -- they are the ones that pin `UNKNOWN` as a
distinct answer from "free", because a cost of zero is the failure mode that
looks like success on every dashboard downstream.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from return_platform.ai.pricing import (
    AICostEstimate,
    AIPricingCatalog,
    AIPricingEntry,
    AIPricingStatus,
)
from return_platform.ai.routing.tasks import load_ai_gateway_configuration

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"


def _entry(
    *,
    version: str,
    provider: str = "GOOGLE",
    model: str = "gemini-2.5-flash",
    effective_from: datetime,
    input_micros: int = 300_000,
    cached_micros: int = 75_000,
    output_micros: int = 2_500_000,
    currency: str = "USD",
) -> AIPricingEntry:
    return AIPricingEntry(
        version=version,
        provider=provider,  # type: ignore[arg-type]
        model=model,
        effectiveFrom=effective_from,
        currency=currency,
        inputPerMillionTokensMicros=input_micros,
        cachedInputPerMillionTokensMicros=cached_micros,
        outputPerMillionTokensMicros=output_micros,
        source="vendor pricing page, read 2026-08-01",
    )


# --- absence is not zero ------------------------------------------------------


def test_an_empty_catalog_reports_unknown_rather_than_free() -> None:
    estimate = AIPricingCatalog().estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=500,
    )

    assert estimate.status is AIPricingStatus.UNKNOWN
    assert estimate.amount_micros is None
    assert estimate.pricing_version is None


def test_a_model_the_catalog_does_not_cover_is_unknown_not_zero() -> None:
    catalog = AIPricingCatalog(
        entries=(_entry(version="p1", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),)
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-3-something-new",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=500,
    )

    assert estimate.status is AIPricingStatus.UNKNOWN
    assert estimate.amount_micros is None


def test_an_attempt_with_no_route_is_unknown() -> None:
    """A safety block or an exhausted route pool records an attempt with no
    provider at all. It has no cost, which is not the same as costing nothing."""
    catalog = AIPricingCatalog(
        entries=(_entry(version="p1", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),)
    )

    estimate = catalog.estimate(
        provider=None,
        model=None,
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
    )

    assert estimate.status is AIPricingStatus.UNKNOWN


def test_a_priced_call_that_used_no_tokens_costs_zero_and_says_so() -> None:
    """The one case where 0 is the truth. It is distinguishable from UNKNOWN by
    `status`, which is the entire point of having a status."""
    catalog = AIPricingCatalog(
        entries=(_entry(version="p1", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),)
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
    )

    assert estimate.status is AIPricingStatus.PRICED
    assert estimate.amount_micros == 0
    assert estimate.pricing_version == "p1"


# --- the version effective at request time ------------------------------------


def test_the_rate_in_force_is_the_latest_one_that_had_already_started() -> None:
    catalog = AIPricingCatalog(
        entries=(
            _entry(
                version="2026-01",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                input_micros=300_000,
            ),
            _entry(
                version="2026-07",
                effective_from=datetime(2026, 7, 1, tzinfo=UTC),
                input_micros=150_000,
            ),
        )
    )

    march = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 3, 15, tzinfo=UTC),
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
    )
    august = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 15, tzinfo=UTC),
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
    )

    # The March call keeps its March price forever. Deriving cost at read time
    # would have re-costed it at 150_000 the day the July rate landed.
    assert march.amount_micros == 300_000
    assert march.pricing_version == "2026-01"
    assert august.amount_micros == 150_000
    assert august.pricing_version == "2026-07"


def test_a_rate_dated_in_the_future_is_published_but_not_yet_charged() -> None:
    catalog = AIPricingCatalog(
        entries=(
            _entry(
                version="now",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                input_micros=300_000,
            ),
            _entry(
                version="later",
                effective_from=datetime(2026, 12, 1, tzinfo=UTC),
                input_micros=100_000,
            ),
        )
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
    )

    assert estimate.pricing_version == "now"


def test_a_call_before_any_published_rate_is_unknown() -> None:
    catalog = AIPricingCatalog(
        entries=(_entry(version="p1", effective_from=datetime(2026, 7, 1, tzinfo=UTC)),)
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 1, 1, tzinfo=UTC),
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=0,
    )

    assert estimate.status is AIPricingStatus.UNKNOWN


# --- the arithmetic -----------------------------------------------------------


def test_cached_input_is_billed_at_the_cached_rate_and_added_not_overlapped() -> None:
    catalog = AIPricingCatalog(
        entries=(
            _entry(
                version="p1",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                input_micros=300_000,
                cached_micros=75_000,
                output_micros=2_500_000,
            ),
        )
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=2_000_000,
        cached_input_tokens=4_000_000,
        output_tokens=1_000_000,
    )

    # 2M * 0.30 + 4M * 0.075 + 1M * 2.50, in micros.
    assert estimate.amount_micros == 600_000 + 300_000 + 2_500_000
    assert estimate.currency == "USD"


def test_a_sub_token_fraction_rounds_half_up_rather_than_to_even() -> None:
    """`round()` would send 0.5 micros to 0 here and to 2 elsewhere. For money
    that is a rounding rule nobody can reconcile against an invoice."""
    catalog = AIPricingCatalog(
        entries=(
            _entry(
                version="p1",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                input_micros=1,
                cached_micros=0,
                output_micros=0,
            ),
        )
    )

    estimate = catalog.estimate(
        provider="GOOGLE",
        model="gemini-2.5-flash",
        at=datetime(2026, 8, 13, tzinfo=UTC),
        input_tokens=500_000,
        cached_input_tokens=0,
        output_tokens=0,
    )

    assert estimate.amount_micros == 1


# --- the catalog is configuration ---------------------------------------------


def test_two_entries_cannot_share_a_version() -> None:
    """A stored `pricing_version` has to name exactly one rate or it explains
    nothing."""
    with pytest.raises(ValidationError):
        AIPricingCatalog(
            entries=(
                _entry(version="p1", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),
                _entry(
                    version="p1",
                    model="gemini-2.5-pro",
                    effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            )
        )


def test_two_rates_cannot_start_for_the_same_model_at_the_same_instant() -> None:
    with pytest.raises(ValidationError):
        AIPricingCatalog(
            entries=(
                _entry(version="a", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),
                _entry(version="b", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),
            )
        )


def test_a_naive_effective_from_is_refused() -> None:
    with pytest.raises(ValidationError):
        _entry(version="p1", effective_from=datetime(2026, 1, 1))  # noqa: DTZ001


def test_the_packaged_configuration_still_loads_and_prices_nothing() -> None:
    """The shipped YAML is untouched by W4.11 and must stay loadable. It carries
    no prices, so a deployment that has published no price list reports UNKNOWN
    rather than inheriting a stale rate somebody committed once."""
    loaded = load_ai_gateway_configuration(CONFIG)

    assert loaded.configuration.pricing.entries == ()


def test_pricing_survives_a_release_round_trip() -> None:
    """Rates travel as part of the AI gateway domain payload, which is what
    makes them versioned runtime config rather than a file read at startup."""
    loaded = load_ai_gateway_configuration(CONFIG)
    priced = loaded.configuration.model_copy(
        update={
            "pricing": AIPricingCatalog(
                entries=(_entry(version="p1", effective_from=datetime(2026, 1, 1, tzinfo=UTC)),)
            )
        }
    )

    payload = priced.model_dump(mode="json")
    restored = type(priced).model_validate(payload)

    assert restored.pricing.entries[0].version == "p1"
    assert restored.pricing.entries[0].effectiveFrom == datetime(2026, 1, 1, tzinfo=UTC)


def test_a_release_written_before_pricing_existed_still_validates() -> None:
    """Every stored release predates this field. Refusing them would take the
    running API down on deploy, and defaulting them to a shipped price list
    would cost historical calls at rates nobody approved."""
    loaded = load_ai_gateway_configuration(CONFIG)
    payload = loaded.configuration.model_dump(mode="json")
    payload.pop("pricing")

    restored = type(loaded.configuration).model_validate(payload)

    assert restored.pricing.entries == ()


def test_unknown_is_constructible_as_one_value() -> None:
    estimate = AICostEstimate.unknown()

    assert estimate.status is AIPricingStatus.UNKNOWN
    assert (estimate.amount_micros, estimate.currency, estimate.pricing_version) == (
        None,
        None,
        None,
    )
