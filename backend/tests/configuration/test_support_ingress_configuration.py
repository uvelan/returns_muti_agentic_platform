"""V2: the released ingress policy says what the contract froze.

Contracts.md sect. 5 and sect. 10. Three properties are worth a test each, and
each of them fails loudly if the block drifts rather than passing on a value
nobody reads.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.configuration.support_ingress_configuration import (
    DEFAULT_INTENTS,
    FALLBACK_INTENT,
    SupportIngressConfiguration,
)


def _released() -> SupportIngressConfiguration:
    configuration: ReturnPlatformConfiguration = load_return_configuration(
        DEFAULT_RETURN_CONFIGURATION_PATH
    ).configuration
    return configuration.support_ingress


def test_the_shipped_configuration_carries_the_block() -> None:
    block = _released()

    # The door starts shut. Asserted rather than assumed: the whole parked
    # lifecycle is dead code if a release ships with it open by accident.
    assert block.nl_enabled is False
    assert block.limits.max_body_characters == 16_000
    assert block.limits.max_messages_per_case_per_window == 60
    assert block.parking.per_case_quota == 50
    assert block.parking.alert_dedupe_window_seconds == 900
    assert block.multi_record_framing_prompt_key == "support-multi-record-do-not-mix"
    assert block.agent_disclosure.display_name
    assert block.agent_disclosure.disclosure_line


def test_the_released_taxonomy_is_the_contracts_taxonomy() -> None:
    """Sect. 5 names nine intents. The release must carry those nine.

    Written as an equality on the whole tuple, in order, rather than as a
    containment check: a release that *added* an intent has widened a closed
    set, and a release that dropped one has removed a branch downstream still
    tests for. Either is drift the contract does not permit a slice to make.
    """
    assert _released().intents == DEFAULT_INTENTS
    assert DEFAULT_INTENTS == (
        "info_request",
        "rma_issued",
        "label_issued",
        "shipping_instruction",
        "tracking_provided",
        "partial_fulfillment",
        "rejection",
        "acknowledgement",
        "other",
    )


def test_the_fallback_intent_is_in_the_guard_set_even_when_a_release_omits_it() -> None:
    """`other` is the floor of the taxonomy, not an entry an operator can delete."""
    narrowed = SupportIngressConfiguration(intents=("info_request",))
    assert FALLBACK_INTENT not in narrowed.intents
    assert FALLBACK_INTENT in narrowed.normalized_intents()
    assert "info_request" in narrowed.normalized_intents()


def test_a_release_without_the_block_still_loads() -> None:
    block = SupportIngressConfiguration()
    assert block.nl_enabled is False
    assert block.intents == DEFAULT_INTENTS
    assert block.outbound_templates == {}


def test_the_block_refuses_a_key_it_does_not_declare() -> None:
    """`extra="forbid"`, so a typo in a release is a failed activation.

    A released `nl_enable: true` that loaded as a default `nl_enabled: false`
    would be an operator who believes the door is open and traffic that is
    silently parking behind it.
    """
    with pytest.raises(ValidationError):
        SupportIngressConfiguration(nl_enable=True)  # type: ignore[call-arg]
