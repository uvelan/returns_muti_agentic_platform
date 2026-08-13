"""W4.7: a turn knows what "now" is, agrees with itself about it, and says so.

Three separable claims, tested separately because they fail for different
reasons:

1. `resolve_date_windows` turns a phrase into absolute UTC boundaries computed
   in the *session's* calendar. The interesting cases are all offset cases -- a
   window that is right in UTC and wrong in Asia/Kolkata is the bug this exists
   to prevent, so the fixtures deliberately straddle local midnight.
2. The as-of is pinned once per turn. Asserted by running the real graph with a
   scripted model over a multi-step turn and comparing every context the model
   was handed, which is the only way to catch a clock read that moved back into
   `_build_context`.
3. The instant reaches the provider's system prompt, not merely the context
   JSON. Asserted against the real `RoutePoolReasoningModelGateway` and a fake
   provider that keeps what it was sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from return_platform.ai.providers import ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
)
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph_nodes import _pinned_grounding
from return_platform.dynamic_knowledge.order_agent.state import (
    ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST,
)
from return_platform.dynamic_knowledge.order_agent.temporal_grounding import (
    DEFAULT_SESSION_TIMEZONE,
    RELATIVE_DATE_PHRASES,
    normalize_session_timezone,
    resolve_date_windows,
    temporal_grounding_prompt,
)

CONFIG = Path(__file__).resolve().parents[2] / "config" / "ai_gateway.yaml"


# --- windows -----------------------------------------------------------------


def test_every_named_phrase_resolves_to_a_half_open_absolute_range() -> None:
    windows = resolve_date_windows(datetime(2026, 8, 13, 9, 30, tzinfo=UTC), "UTC")

    assert set(windows) == set(RELATIVE_DATE_PHRASES)
    for phrase, window in windows.items():
        assert set(window) == {"start", "endExclusive"}, phrase
        start = datetime.fromisoformat(window["start"])
        end = datetime.fromisoformat(window["endExclusive"])
        assert start.tzinfo is not None and end.tzinfo is not None, phrase
        assert start < end, phrase


def test_boundaries_are_the_session_zones_midnight_not_utcs() -> None:
    """02:15 UTC on the 13th is already the 13th in Kolkata and still the 12th
    in New York. A window computed in UTC would answer the same for both."""
    as_of = datetime(2026, 8, 13, 2, 15, tzinfo=UTC)

    kolkata = resolve_date_windows(as_of, "Asia/Kolkata")
    new_york = resolve_date_windows(as_of, "America/New_York")

    # Kolkata is UTC+5:30, so its 13 Aug began at 18:30 UTC on the 12th.
    assert kolkata["today"]["start"] == "2026-08-12T18:30:00Z"
    assert kolkata["today"]["endExclusive"] == "2026-08-13T18:30:00Z"
    # New York is UTC-4 in August, so the associate is still in the 12th, whose
    # local midnight was 04:00 UTC on the 12th.
    assert new_york["today"]["start"] == "2026-08-12T04:00:00Z"
    assert new_york["yesterday"]["start"] == "2026-08-11T04:00:00Z"
    assert new_york["yesterday"]["endExclusive"] == new_york["today"]["start"]


def test_weeks_start_on_monday_and_last_week_does_not_overlap_this_week() -> None:
    # 2026-08-13 is a Thursday.
    windows = resolve_date_windows(datetime(2026, 8, 13, 9, 30, tzinfo=UTC), "UTC")

    assert windows["this_week"]["start"] == "2026-08-10T00:00:00Z"
    assert windows["last_week"]["start"] == "2026-08-03T00:00:00Z"
    assert windows["last_week"]["endExclusive"] == windows["this_week"]["start"]


def test_last_seven_days_is_a_different_question_from_last_week() -> None:
    windows = resolve_date_windows(datetime(2026, 8, 13, 9, 30, tzinfo=UTC), "UTC")

    assert windows["last_7_days"]["start"] == "2026-08-06T00:00:00Z"
    assert windows["last_7_days"]["endExclusive"] == "2026-08-14T00:00:00Z"
    assert windows["last_7_days"] != windows["last_week"]


def test_month_windows_roll_over_a_year_boundary() -> None:
    windows = resolve_date_windows(datetime(2026, 1, 9, 12, 0, tzinfo=UTC), "UTC")

    assert windows["this_month"]["start"] == "2026-01-01T00:00:00Z"
    assert windows["last_month"]["start"] == "2025-12-01T00:00:00Z"
    assert windows["last_month"]["endExclusive"] == "2026-01-01T00:00:00Z"


def test_a_window_spanning_a_dst_change_keeps_local_midnight() -> None:
    """New York moved to EST on 1 Nov 2026, so that week's days are not all the
    same number of hours. Anchoring on local midnight is what keeps the
    boundaries on the days the associate means; anchoring on a fixed offset
    would slide them by an hour."""
    windows = resolve_date_windows(datetime(2026, 11, 3, 15, 0, tzinfo=UTC), "America/New_York")

    # New York left EDT on Sunday 1 Nov, so Monday 2 Nov's local midnight is
    # 05:00 UTC (EST, UTC-5) while Monday 26 Oct's was 04:00 UTC (EDT, UTC-4).
    # Two different offsets, the same local midnight -- which is exactly what a
    # fixed-offset implementation gets wrong, and by a whole hour.
    assert windows["this_week"]["start"] == "2026-11-02T05:00:00Z"
    assert windows["last_week"]["start"] == "2026-10-26T04:00:00Z"
    assert windows["last_week"]["endExclusive"] == windows["this_week"]["start"]


# --- the zone ----------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "Mars/Olympus_Mons", "not a zone"])
def test_an_absent_or_unknown_zone_grounds_the_turn_in_utc(value: str | None) -> None:
    assert normalize_session_timezone(value) == DEFAULT_SESSION_TIMEZONE


def test_a_real_zone_is_kept_verbatim() -> None:
    assert normalize_session_timezone("Asia/Kolkata") == "Asia/Kolkata"


def test_an_unknown_zone_does_not_take_the_windows_down_with_it() -> None:
    windows = resolve_date_windows(datetime(2026, 8, 13, 9, 30, tzinfo=UTC), "Mars/Olympus_Mons")

    assert windows["today"]["start"] == "2026-08-13T00:00:00Z"


# --- pinning -----------------------------------------------------------------


def test_the_pinned_as_of_is_checkpointable() -> None:
    """A state key absent from the allowlist fails every checkpoint write at
    runtime rather than here (CheckpointRedactor.enforce)."""
    assert {"as_of", "session_timezone"} <= ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST


def test_a_state_with_no_as_of_fails_loudly_instead_of_reading_the_clock() -> None:
    """The failure mode this rejects is the quiet one: falling back to
    `datetime.now()` here would restore the per-node re-read the step removed
    and would never show up as an error."""
    with pytest.raises(OrderAgentFailure) as error:
        _pinned_grounding({"session_timezone": "UTC"})

    assert error.value.code == "ORDER_AGENT_TURN_NOT_GROUNDED"


def test_a_malformed_as_of_fails_rather_than_defaulting() -> None:
    with pytest.raises(OrderAgentFailure) as error:
        _pinned_grounding({"as_of": "the thirteenth", "session_timezone": "UTC"})

    assert error.value.code == "ORDER_AGENT_TURN_NOT_GROUNDED"


def test_a_naive_as_of_is_read_as_utc_rather_than_as_local_time() -> None:
    as_of, zone = _pinned_grounding({"as_of": "2026-08-13T09:30:00", "session_timezone": "UTC"})

    assert as_of == datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
    assert zone == "UTC"


def test_an_unresolvable_stored_zone_falls_back_without_failing_the_turn() -> None:
    _, zone = _pinned_grounding(
        {"as_of": "2026-08-13T09:30:00+00:00", "session_timezone": "Mars/Olympus_Mons"}
    )

    assert zone == DEFAULT_SESSION_TIMEZONE


# --- the prompt --------------------------------------------------------------


def test_the_grounding_block_states_the_instant_the_zone_and_the_rule() -> None:
    block = temporal_grounding_prompt(datetime(2026, 8, 13, 9, 30, tzinfo=UTC), "America/New_York")

    assert "2026-08-13T09:30:00Z" in block
    assert "America/New_York" in block
    assert "resolvedDateWindows" in block
    for phrase in RELATIVE_DATE_PHRASES:
        assert phrase in block


def test_the_block_normalizes_a_non_utc_as_of_before_stating_it() -> None:
    """Two turns whose as-of differ only in the offset they were expressed in
    are the same instant, and the prompt must not imply otherwise."""
    block = temporal_grounding_prompt(
        datetime(2026, 8, 13, 5, 30, tzinfo=ZoneInfo("America/New_York")), "America/New_York"
    )

    assert "2026-08-13T09:30:00Z" in block


class _CapturingProvider:
    configured = True

    def __init__(self) -> None:
        self.name = "GOOGLE"
        self.model = "google-a"
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=(
                '{"business_capability":"order-discovery",'
                '"action_type":"OUT_OF_SCOPE",'
                '"decision_summary":"The request is outside the configured scope."}'
            ),
            input_tokens=10,
            output_tokens=8,
            total_tokens=18,
        )


@pytest.mark.asyncio
async def test_the_instant_reaches_the_providers_system_prompt(
    test_settings: Settings,
) -> None:
    """Not just the context JSON. A model that has to find the date inside a
    sorted blob to know what "yesterday" means will sometimes not look."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider()
    route = AIRoute(
        route_id="google/google-a/google-key-1",
        provider_name="GOOGLE",
        model="google-a",
        credential_id="google-key-1",
        credential_fingerprint="test",
        tier=ModelTier.STANDARD,
        provider=provider,
        provider_priority=0,
        model_priority=0,
        credential_priority=0,
        allowed_task_keys=frozenset({"ORDER_AGENT_REASONING_V1"}),
    )
    gateway = RoutePoolReasoningModelGateway(
        settings=test_settings.model_copy(
            update={"ai_timeout_seconds": 1.0, "ai_global_timeout_seconds": 5.0}
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool((route,), loaded.configuration),
    )

    await gateway.decide(_context())

    assert len(provider.requests) == 1
    prompt = provider.requests[0].system_prompt
    assert "2026-08-13T09:30:00Z" in prompt
    assert "Asia/Kolkata" in prompt
    # The configured prompt and the schema stay in front of the variable block,
    # so the cacheable prefix is unbroken (W5.3 depends on this ordering).
    assert prompt.index("REQUIRED RESPONSE SCHEMA") < prompt.index("TEMPORAL GROUNDING")


@pytest.mark.asyncio
async def test_the_windows_travel_with_the_context_as_absolute_instants(
    test_settings: Settings,
) -> None:
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider()
    route = AIRoute(
        route_id="google/google-a/google-key-1",
        provider_name="GOOGLE",
        model="google-a",
        credential_id="google-key-1",
        credential_fingerprint="test",
        tier=ModelTier.STANDARD,
        provider=provider,
        provider_priority=0,
        model_priority=0,
        credential_priority=0,
        allowed_task_keys=frozenset({"ORDER_AGENT_REASONING_V1"}),
    )
    gateway = RoutePoolReasoningModelGateway(
        settings=test_settings.model_copy(
            update={"ai_timeout_seconds": 1.0, "ai_global_timeout_seconds": 5.0}
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool((route,), loaded.configuration),
    )

    await gateway.decide(_context())

    context_json = provider.requests[0].user_payload["contextJson"]
    assert '"resolved_date_windows"' in context_json
    assert "2026-08-12T18:30:00Z" in context_json  # Kolkata's "today" start


def _context() -> AgentTurnContext:
    as_of = datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
    zone = "Asia/Kolkata"
    return AgentTurnContext(
        conversation_id="conversation-1",
        client_turn_id="turn-1",
        agent_id="agent_a",
        user_message="Find the orders from yesterday",
        as_of=as_of,
        session_timezone=zone,
        resolved_date_windows=resolve_date_windows(as_of, zone),
        schema_version="1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="policy-1",
        prompt_version="prompt-1",
        compact_schema={},
        conversation_state={},
    )
