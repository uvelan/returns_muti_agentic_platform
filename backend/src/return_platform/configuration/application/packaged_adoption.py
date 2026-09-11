"""Merge packaged configuration files into an active release: one decision.

CFG-3a. This was `bootstrap_graph_configuration.main`'s own decision body,
inline -- the carry-forward described at length in this module's functions
below, run once per CLI publish. `POST /api/config/adopt-packaged` and
`GET /api/config/packaged-drift` need the SAME decision, not a compatible
one: the CLI publishes what a deployment boots with, the API lets an
operator resolve the same undecidable keys through the Configuration screen,
and if the two ever disagreed about which key an edit belongs to, an
operator's answer through the API could be silently overwritten by the next
`runtime-configuration-init`. `bootstrap_graph_configuration.main` now calls
`adopt_packaged_configuration` too, so there is exactly one place this
decision is made.

**Moved, not reimplemented.** Every helper below (`_units`, `_assemble`,
`_adopt_requests`, `_key_digests`, `_fill_absent_leaves`, `_carry_forward`,
`_drop_retired_keys`) and the two metadata keys (`PACKAGED_KEY_DIGESTS`,
`PACKAGED_DOMAIN_KEY_DIGESTS`) lived in `cli/bootstrap_graph_configuration.py`
until this lease; `bootstrap_graph_configuration` now imports them from here
so `bootstrap_graph_configuration.<name>` still resolves for every existing
caller and test -- the module attribute is unchanged even though the
definition moved. Nothing about their behaviour changed in the move; only
`adopt_packaged_configuration` itself is new, and it is the same sequence of
operations `main()` used to run inline, unmodified in order or effect.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CARRY_FORWARD_SPLIT_KEYS",
    "PACKAGED_DOMAIN_KEY_DIGESTS",
    "PACKAGED_KEY_DIGESTS",
    "PackagedAdoptionResult",
    "adopt_packaged_configuration",
]

#: Release metadata key holding a digest of each top-level value in the PACKAGED
#: configuration at the moment the release was published.
#:
#: This is the baseline that makes the carry-forward below decidable. Without it
#: a publish can see that the release and the packaged file disagree about a key
#: and cannot tell which of the two changed -- an operator edited the release, or
#: the file moved on since the release was cut. Guessing "the release" froze every
#: packaged change out of every deployment that had ever published a release;
#: guessing "the file" would silently undo operator edits on every restart.
PACKAGED_KEY_DIGESTS = "packaged_key_digests"

#: Release metadata key holding the same baseline for the OTHER two domains,
#: keyed by domain: `{"AI_GATEWAY": {unit: digest}, "DEPENDENCY_SIMULATION": {...}}`.
#:
#: Until this existed those domains were not carried forward at all -- every
#: publish took `ai_gateway.yaml` and `dependency_simulation.yaml` whole, so an
#: AI task edited in the AI Control Center went back to the file on the next
#: stack start. Observed 2026-09-11: `maximumOutputTokens` set to 1234 through
#: the API, one bootstrap run later it read 192, the file's value.
PACKAGED_DOMAIN_KEY_DIGESTS = "packaged_domain_key_digests"

#: The mapping keys inside each domain whose ENTRIES carry forward one by one.
#:
#: A domain's top-level keys are its units, as for the business domain -- except
#: that `tasks` is one key holding every AI task and `dependencies` one key
#: holding every simulated system. Deciding those whole would let one edited
#: task freeze the file's changes to every other task, so each entry is its own
#: unit: `tasks.RETURN_STATUS_SUMMARY_V1`, `dependencies.OMC`.
CARRY_FORWARD_SPLIT_KEYS: dict[str, tuple[str, ...]] = {
    AI_GATEWAY_DOMAIN_KEY: ("tasks",),
    DEPENDENCY_SIMULATION_DOMAIN_KEY: ("dependencies",),
}


def _units(payload: Mapping[str, Any], split_keys: tuple[str, ...]) -> dict[str, Any]:
    """A payload as carry-forward units: top-level keys, split keys by entry."""
    units: dict[str, Any] = {}
    for key, value in payload.items():
        if key in split_keys and isinstance(value, dict) and value:
            for entry, entry_value in value.items():
                units[f"{key}.{entry}"] = entry_value
        else:
            units[key] = value
    return units


def _assemble(units: Mapping[str, Any], split_keys: tuple[str, ...]) -> dict[str, Any]:
    """The inverse of `_units`."""
    payload: dict[str, Any] = {}
    for unit, value in units.items():
        head, dot, entry = unit.partition(".")
        if head in split_keys:
            if dot:
                payload.setdefault(head, {})[entry] = value
            elif isinstance(value, dict):
                payload.setdefault(head, {}).update(value)
            else:
                # A split key whose value is not a mapping (the model would
                # refuse it, but the merge must not lose it on the way there).
                payload[head] = value
        else:
            payload[unit] = value
    return payload


def _adopt_requests(adopt_packaged_keys: tuple[str, ...]) -> dict[str, set[str]]:
    """`--adopt-packaged-key` values by domain.

    A bare key names a business-domain key (`discovery`); a qualified one names a
    unit of another domain (`AI_GATEWAY/tasks.RETURN_STATUS_SUMMARY_V1`,
    `DEPENDENCY_SIMULATION/dependencies.OMC`).
    """
    requests: dict[str, set[str]] = {}
    known_domains = {
        RETURN_PLATFORM_DOMAIN_KEY,
        AI_GATEWAY_DOMAIN_KEY,
        DEPENDENCY_SIMULATION_DOMAIN_KEY,
    }
    for raw in adopt_packaged_keys:
        domain, slash, unit = raw.partition("/")
        if not slash:
            domain, unit = RETURN_PLATFORM_DOMAIN_KEY, raw
        if domain not in known_domains or not unit:
            raise ValueError(
                f"adopt-packaged-key {raw!r} does not name a configuration domain unit; "
                f"expected <key> or <DOMAIN>/<unit> with DOMAIN one of {sorted(known_domains)}"
            )
        requests.setdefault(domain, set()).add(unit)
    return requests


def _key_digests(payload: Mapping[str, Any]) -> dict[str, str]:
    """One digest per top-level key, over its canonical JSON."""
    return {
        key: hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for key, value in payload.items()
    }


def _fill_absent_leaves(packaged_value: Any, active_value: Any) -> Any:
    """The active value, plus every mapping entry the packaged value has and it lacks.

    Recursive over mappings only. A leaf both sides carry keeps the active
    value -- that is the undecidable case, and the release wins it -- but a leaf
    the release does not carry at all is most often a key the file gained after
    the release was cut (a new agent block, a new ship-via code), and leaving it
    out froze every such addition out of a deployment that had no baseline. The
    one thing this cannot tell apart is an operator who deleted a mapping entry
    the file still carries: that entry comes back on the next publish. So a key
    that needed filling is NOT treated as decided by the caller -- it is named
    in the warning and no baseline is recorded for it -- and the operator's
    answer to a deliberate deletion is `--adopt-packaged-key` once (which
    records the baseline) followed by the deletion, which then survives because
    the key's digest moves away from the baseline.
    """
    if not isinstance(packaged_value, dict) or not isinstance(active_value, dict):
        return active_value
    filled = dict(active_value)
    for key, value in packaged_value.items():
        if key not in filled:
            filled[key] = value
        else:
            filled[key] = _fill_absent_leaves(value, filled[key])
    return filled


def _carry_forward(
    packaged: Mapping[str, Any],
    active_payload: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...], dict[str, str]]:
    """Combine the packaged file with the active release, and say what it dropped.

    Returns the merged payload, the keys the packaged file changed that could
    NOT be adopted -- empty whenever the answer is exact -- and the baseline
    digests the publish may truthfully record: one per top-level key whose
    published value is known to be the packaged file's, or whose baseline was
    already recorded.

    With a baseline entry for a key the decision needs no judgement: a release
    value that still matches what the packaged file said when the release was cut
    was never edited, so the file's new value replaces it; a value that has moved
    away from that baseline was changed by someone after the file was read -- an
    operator through the config API, or this bootstrap writing AI receipts into
    `runtime_integrations` -- and is kept.

    Without a baseline entry a key is decided only where no judgement is needed:
    the release does not carry it (the file's value is adopted), or the release
    carries exactly the file's value (nothing to decide). Any other key keeps the
    release's values, gains the leaves the release lacks (see
    `_fill_absent_leaves`), and is named to the caller rather than dropped in
    silence -- including when filling was the only difference, because a leaf
    the release lacks may be one an operator deleted.

    The baseline is recorded PER KEY, and only for keys this run decided. A key
    left undecided stays undecided on the next run rather than being stamped as
    an operator edit -- which is what recording the whole file would do, and
    what froze packaged changes out for good on every deployment whose first
    release predated baselines: no baseline could be recorded while any key was
    undecidable, so none ever was, and "at most once" became "every start".
    """
    known = dict(baseline or {})
    packaged_digests = _key_digests(packaged)
    active_digests = _key_digests(active_payload)
    merged: dict[str, Any] = {}
    unadopted: list[str] = []
    recordable: dict[str, str] = {}
    for key, value in packaged.items():
        if key not in active_payload:
            merged[key] = value
        elif key in known:
            merged[key] = value if active_digests[key] == known[key] else active_payload[key]
        elif active_payload[key] == value:
            merged[key] = value
        else:
            merged[key] = _fill_absent_leaves(value, active_payload[key])
            unadopted.append(key)
        if key in known or (key not in unadopted and merged[key] == value):
            recordable[key] = packaged_digests[key]
    for key, value in active_payload.items():
        merged.setdefault(key, value)
    return merged, tuple(sorted(unadopted)), recordable


def _drop_retired_keys(merged_payload: dict[str, Any]) -> dict[str, Any]:
    """Drop top-level keys the model no longer declares, before validation.

    `_carry_forward`'s last loop (`merged.setdefault(key, value)` over the
    ACTIVE release) is exactly how a key the packaged file dropped -- because
    the model retired it, not because an operator deleted it -- survives into
    `merged_payload` from an old release that still carries it. Every process
    that has ever published a release since (`feature_flags`/`extensions`,
    retired in CFG-1) would carry it forward forever, and
    `ReturnPlatformConfiguration.model_validate` (`StrictConfigModel` forbids
    extra keys) would then refuse the very release the bootstrap is trying to
    republish -- on every deployment that had ever adopted one, not just this
    one. Without this drop the failure path in `main()` below falls back to
    the packaged baseline, silently discarding every operator value the
    active release carried, which is the deadlock this function exists to
    prevent: a release can never again pass `model_validate` while it still
    carries a key the model does not declare, because nothing sitting between
    the graph and this function removes one.

    Not the same case as an operator's key. An operator's edit is inside a
    key the model still declares (`_carry_forward`/`_fill_absent_leaves`
    handle that); this is a whole top-level key the model no longer has an
    opinion about at all.
    """
    declared = set(ReturnPlatformConfiguration.model_fields)
    for key in sorted(set(merged_payload) - declared):
        logger.warning("retired_configuration_key key=%s", key)
        del merged_payload[key]
    return merged_payload


@dataclass(frozen=True, slots=True)
class PackagedAdoptionResult:
    """Per-domain outcome of merging packaged configuration into a release.

    `merged_domains` holds domain payloads ready to store, keyed the way a
    release's domain map is keyed. AI_GATEWAY and DEPENDENCY_SIMULATION are
    always present -- they fall back to the packaged domain unchanged exactly
    where `main()`'s old inline `continue` left `carried_domains[domain_key]`
    at its initial packaged value. RETURN_PLATFORM is present only when there
    was something to merge AND the merge validated; its absence is exactly
    the case the old inline `existing_configuration` stayed `None` in, and a
    caller that needs the "what does the deployment boot with" answer falls
    back to the packaged configuration itself, the same way `main()` does
    with `existing_configuration or loaded.configuration`.

    `existing_return_platform_configuration` is the same RETURN_PLATFORM
    outcome as a validated model instance, for a caller (the CLI) that needs
    one without re-validating JSON this function already validated.

    `undecided` names, per domain, the keys or units this run could not
    adopt -- present with an empty tuple for a domain with nothing
    undecided, so a caller can render "0 undecided" without treating a
    domain's absence from the mapping as a distinct third state.
    """

    merged_domains: dict[str, dict[str, Any]] = field(default_factory=dict)
    existing_return_platform_configuration: ReturnPlatformConfiguration | None = None
    recordable_baseline: dict[str, str] = field(default_factory=dict)
    recordable_domain_baselines: dict[str, dict[str, str]] = field(default_factory=dict)
    undecided: dict[str, tuple[str, ...]] = field(default_factory=dict)


def adopt_packaged_configuration(
    *,
    packaged_return_platform: Mapping[str, Any],
    packaged_domains: Mapping[str, Mapping[str, Any]],
    active_return_platform: Mapping[str, Any] | None,
    active_domains: Mapping[str, Mapping[str, Any]],
    active_metadata: Mapping[str, Any],
    adopt_packaged: bool = False,
    adopt_packaged_keys: tuple[str, ...] = (),
) -> PackagedAdoptionResult:
    """Merge packaged configuration into an active release: one decision.

    `packaged_return_platform` and `packaged_domains` (keyed
    `AI_GATEWAY`/`DEPENDENCY_SIMULATION`) are `model_dump(mode="json")` of the
    packaged files, already loaded -- this function does no file I/O, so a
    caller with no filesystem access to the packaged directory (the API
    process, which reads it from `app.state`) can call it the same as the
    CLI. `active_return_platform`/`active_domains` are the ACTIVE release's
    stored domain payloads (`None`/absent when the release does not carry
    that domain, or there is no active release at all), and `active_metadata`
    is that release's metadata dict (holding `PACKAGED_KEY_DIGESTS` /
    `PACKAGED_DOMAIN_KEY_DIGESTS` when a previous publish recorded one).

    `adopt_packaged`/`adopt_packaged_keys` are `--adopt-packaged`/
    `--adopt-packaged-key`, unchanged: the operator's explicit answer to an
    undecidable key, checked for an unknown key or domain UNCONDITIONALLY --
    before anything about the active release is examined -- because a typo in
    a key name must fail the same way whether or not a release exists yet
    (RV F5 on CFG-0).

    Raises `ValueError` for an unknown adopted key/unit/domain, exactly as
    `main()`'s inline checks did; raises nothing else -- a domain that no
    longer validates after merging is logged and fallen back to, never
    raised, because a publish (or an API read) must not fail outright over
    one domain's stale release.
    """
    packaged_payload = dict(packaged_return_platform)
    recordable_baseline: dict[str, str] = _key_digests(packaged_payload)
    adopt_requests = _adopt_requests(adopt_packaged_keys)
    adopted_keys = set(adopt_requests.get(RETURN_PLATFORM_DOMAIN_KEY, ()))
    if adopt_packaged:
        adopted_keys.update(packaged_payload)
    unknown_keys = sorted(adopted_keys - set(packaged_payload))
    if unknown_keys:
        raise ValueError(
            f"adopt-packaged-key names units {RETURN_PLATFORM_DOMAIN_KEY} does not have: "
            + ", ".join(unknown_keys)
        )

    merged_domains: dict[str, dict[str, Any]] = {}
    existing_configuration: ReturnPlatformConfiguration | None = None
    undecided: dict[str, tuple[str, ...]] = {}

    if active_return_platform is not None:
        baseline = active_metadata.get(PACKAGED_KEY_DIGESTS)
        merged_payload, unadopted, recordable_baseline = _carry_forward(
            packaged_payload, dict(active_return_platform), baseline
        )
        packaged_digests = _key_digests(packaged_payload)
        for key in adopted_keys:
            merged_payload[key] = packaged_payload[key]
            recordable_baseline[key] = packaged_digests[key]
        unadopted = tuple(key for key in unadopted if key not in adopted_keys)
        undecided[RETURN_PLATFORM_DOMAIN_KEY] = unadopted
        if unadopted:
            logger.warning(
                "packaged_configuration_not_adopted domain=%s keys=%s; these keys have "
                "no recorded baseline and the release and the packaged file disagree "
                "inside them, so an operator's edit and a change to the file cannot be "
                "told apart and the release wins (leaves the release lacks were filled "
                "from the file). Every other key now carries a baseline and decides "
                "itself from here on. Re-run with --adopt-packaged-key <key> to take "
                "the packaged file for one of these keys, or --adopt-packaged for all "
                "of them.",
                RETURN_PLATFORM_DOMAIN_KEY,
                ",".join(unadopted),
            )
        merged_payload = _drop_retired_keys(merged_payload)
        try:
            existing_configuration = ReturnPlatformConfiguration.model_validate(merged_payload)
        except ValidationError as error:
            logger.error(
                "active_release_no_longer_validates domain=%s errors=%d; falling back to "
                "the packaged configuration. Operator values carried by that release are "
                "NOT preserved -- re-apply them after this publish. Detail: %s",
                RETURN_PLATFORM_DOMAIN_KEY,
                error.error_count(),
                error,
            )
        else:
            merged_domains[RETURN_PLATFORM_DOMAIN_KEY] = existing_configuration.model_dump(
                mode="json"
            )
    else:
        undecided[RETURN_PLATFORM_DOMAIN_KEY] = ()

    domain_models: dict[str, type[AIGatewayConfiguration | DependencySimulationConfiguration]] = {
        AI_GATEWAY_DOMAIN_KEY: AIGatewayConfiguration,
        DEPENDENCY_SIMULATION_DOMAIN_KEY: DependencySimulationConfiguration,
    }
    carried_domains: dict[str, dict[str, Any]] = {
        domain_key: dict(payload) for domain_key, payload in packaged_domains.items()
    }
    recordable_domain_baselines: dict[str, dict[str, str]] = {}
    domain_baselines: Mapping[str, Any] = active_metadata.get(PACKAGED_DOMAIN_KEY_DIGESTS) or {}

    for domain_key, packaged_domain in packaged_domains.items():
        split_keys = CARRY_FORWARD_SPLIT_KEYS[domain_key]
        packaged_units = _units(packaged_domain, split_keys)
        recordable_domain_baselines[domain_key] = _key_digests(packaged_units)
        requested_units = set(adopt_requests.get(domain_key, ()))
        if adopt_packaged:
            requested_units.update(packaged_units)
        unknown_units = sorted(requested_units - set(packaged_units))
        if unknown_units:
            raise ValueError(
                f"adopt-packaged-key names units {domain_key} does not have: "
                + ", ".join(unknown_units)
            )
        active_domain = active_domains.get(domain_key)
        if active_domain is None:
            undecided[domain_key] = ()
            continue
        merged_units, unadopted_units, recordable = _carry_forward(
            packaged_units,
            _units(active_domain, split_keys),
            domain_baselines.get(domain_key),
        )
        packaged_unit_digests = _key_digests(packaged_units)
        for unit in requested_units:
            merged_units[unit] = packaged_units[unit]
            recordable[unit] = packaged_unit_digests[unit]
        unadopted_units = tuple(u for u in unadopted_units if u not in requested_units)
        merged_domain = _assemble(merged_units, split_keys)
        try:
            validated_domain = domain_models[domain_key].model_validate(merged_domain)
        except ValidationError as error:
            logger.error(
                "active_domain_no_longer_validates domain=%s errors=%d; falling back to "
                "the packaged configuration for this domain. Operator values carried by "
                "that release are NOT preserved -- re-apply them after this publish. "
                "Detail: %s",
                domain_key,
                error.error_count(),
                error,
            )
            undecided[domain_key] = unadopted_units
            continue
        carried_domains[domain_key] = validated_domain.model_dump(mode="json")
        recordable_domain_baselines[domain_key] = recordable
        undecided[domain_key] = unadopted_units
        if unadopted_units:
            logger.warning(
                "packaged_configuration_not_adopted domain=%s keys=%s; these units have "
                "no recorded baseline and the release and the packaged file disagree "
                "inside them, so an operator's edit and a change to the file cannot be "
                "told apart and the release wins. Re-run with --adopt-packaged-key %s/"
                "<unit> to take the packaged file for one of them.",
                domain_key,
                ",".join(unadopted_units),
                domain_key,
            )

    merged_domains[AI_GATEWAY_DOMAIN_KEY] = carried_domains[AI_GATEWAY_DOMAIN_KEY]
    merged_domains[DEPENDENCY_SIMULATION_DOMAIN_KEY] = carried_domains[
        DEPENDENCY_SIMULATION_DOMAIN_KEY
    ]

    return PackagedAdoptionResult(
        merged_domains=merged_domains,
        existing_return_platform_configuration=existing_configuration,
        recordable_baseline=recordable_baseline,
        recordable_domain_baselines=recordable_domain_baselines,
        undecided=undecided,
    )
