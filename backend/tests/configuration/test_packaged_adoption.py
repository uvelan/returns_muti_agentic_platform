"""Unit tests for `configuration/application/packaged_adoption.py`.

Focused on `summarize_packaged_drift`/`_would_adopt` -- RV CFG-3a round 1
finding F1. `test_graph_configuration_bootstrap.py` covers
`adopt_packaged_configuration`'s carry-forward decision itself (via the CLI
`main()` it backs); this file covers the read-only drift summary the API's
`GET /packaged-drift` serves, which has no CLI equivalent to borrow coverage
from.

RETURN_PLATFORM payloads here are the real packaged document with one field
mutated, not a toy dict -- `active_return_platform is not None` runs the
merge through `ReturnPlatformConfiguration.model_validate`, so a made-up
`{"threshold": ...}` payload fails validation before the assertion under
test is ever reached (a toy scenario reported "12 validation errors" and a
`KeyError` on `merged_domains`, not the drift bug). The same construction
`test_graph_configuration_bootstrap.py` already uses.
"""

from __future__ import annotations

from typing import Any

from return_platform.configuration.application.packaged_adoption import (
    PACKAGED_KEY_DIGESTS,
    _key_digests,
    adopt_packaged_configuration,
    summarize_packaged_drift,
)
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)

_NO_OTHER_DOMAINS: dict[str, dict[str, Any]] = {
    AI_GATEWAY_DOMAIN_KEY: {},
    DEPENDENCY_SIMULATION_DOMAIN_KEY: {},
}


def _packaged_return_platform() -> dict[str, Any]:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration.model_dump(
        mode="json"
    )


def test_would_adopt_agrees_with_the_merge_for_a_baselined_key_an_operator_edited() -> None:
    """RV CFG-3a round 1 F1.

    `discovery` carries a baseline recorded against an OLDER value of the
    packaged file (an empty `identification_fields`). The active release has
    since been edited to a ONE-ITEM list -- moving its digest away from that
    baseline. The packaged file has independently moved to its real, full
    list. `_carry_forward`'s "moved away from the baseline" rule (its own
    docstring) says the release wins: `merged["discovery"]` stays the
    one-item list, and the key is DECIDED, not undecided -- `_carry_forward`
    never appends a `key in known` decision to `unadopted`.

    The bug: deriving `would_adopt` from "not undecided" alone read that
    decided-in-the-release's-favour key as "the file was taken", because
    `discovery` is not undecided AND `active != packaged`. It reported
    `would_adopt = ('discovery',)` while the merge kept the one-item list.
    Deriving `would_adopt` from the merge itself cannot make this mistake.
    """
    packaged = _packaged_return_platform()
    full_fields = packaged["discovery"]["identification_fields"]
    assert len(full_fields) > 1, "fixture needs a multi-entry identification_fields to mutate"

    cut_time_discovery = {**packaged["discovery"], "identification_fields": []}
    baseline_digest = _key_digests({"discovery": cut_time_discovery})["discovery"]
    metadata = {PACKAGED_KEY_DIGESTS: {"discovery": baseline_digest}}

    edited_fields = full_fields[:1]
    active = {
        **packaged,
        "discovery": {**packaged["discovery"], "identification_fields": edited_fields},
    }

    result = adopt_packaged_configuration(
        packaged_return_platform=packaged,
        packaged_domains=_NO_OTHER_DOMAINS,
        active_return_platform=active,
        active_domains={},
        active_metadata=metadata,
    )

    # The merge keeps the operator's edit -- proves the fixture reproduces
    # the "moved away from the baseline" branch, not some other path.
    assert (
        result.merged_domains[RETURN_PLATFORM_DOMAIN_KEY]["discovery"]["identification_fields"]
        == edited_fields
    )
    assert "discovery" not in result.undecided[RETURN_PLATFORM_DOMAIN_KEY]

    drift = summarize_packaged_drift(
        packaged_return_platform=packaged,
        packaged_domains=_NO_OTHER_DOMAINS,
        active_return_platform=active,
        active_domains={},
        active_metadata=metadata,
    )

    assert "discovery" not in drift[RETURN_PLATFORM_DOMAIN_KEY].would_adopt
    assert "discovery" not in drift[RETURN_PLATFORM_DOMAIN_KEY].undecided


def test_would_adopt_still_reports_a_key_the_merge_actually_took() -> None:
    """The positive case, so the fix is not simply "always empty": a key
    the release predates (not carried at all) is adopted from the packaged
    file, and `would_adopt` must still say so."""
    packaged = _packaged_return_platform()
    active = dict(packaged)
    del active["copilot"]

    result = adopt_packaged_configuration(
        packaged_return_platform=packaged,
        packaged_domains=_NO_OTHER_DOMAINS,
        active_return_platform=active,
        active_domains={},
        active_metadata={},
    )
    assert result.merged_domains[RETURN_PLATFORM_DOMAIN_KEY]["copilot"] == packaged["copilot"]

    drift = summarize_packaged_drift(
        packaged_return_platform=packaged,
        packaged_domains=_NO_OTHER_DOMAINS,
        active_return_platform=active,
        active_domains={},
        active_metadata={},
    )

    assert "copilot" in drift[RETURN_PLATFORM_DOMAIN_KEY].would_adopt


def test_would_adopt_is_empty_when_there_is_no_active_release() -> None:
    """No release to compare against -- nothing has been merged yet, so
    `would_adopt` reports nothing rather than guessing at a fresh publish."""
    packaged = _packaged_return_platform()

    drift = summarize_packaged_drift(
        packaged_return_platform=packaged,
        packaged_domains=_NO_OTHER_DOMAINS,
        active_return_platform=None,
        active_domains={},
        active_metadata={},
    )

    assert drift[RETURN_PLATFORM_DOMAIN_KEY].would_adopt == ()
    assert drift[RETURN_PLATFORM_DOMAIN_KEY].undecided == ()
