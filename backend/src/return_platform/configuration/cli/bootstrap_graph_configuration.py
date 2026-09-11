"""Publish the runtime configuration as an active graph release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from neo4j import AsyncGraphDatabase
from pydantic import ValidationError

from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.bootstrap_runtime_integrations import (
    begin_ai_validation_run,
    build_bootstrap_runtime_configuration,
    build_configured_runtime_configuration,
    finish_ai_validation_run,
)
from return_platform.configuration.graph_repository import (
    Neo4jConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
    load_dependency_simulation_configuration,
)
from return_platform.secrets.runtime import (
    resolve_runtime_settings_from_vault,
)
from return_platform.secrets.vault import SecretResolver

logger = logging.getLogger(__name__)

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
            container = payload.setdefault(head, {})
            if dot:
                container[entry] = value
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
    the release does not carry at all cannot be an operator's edit of anything:
    it is a key the file gained after the release was cut (a new agent block, a
    new ship-via code), and leaving it out froze every such addition out of a
    deployment that had no baseline. The one thing this cannot tell apart is an
    operator who deleted a mapping entry the file still carries; that entry
    comes back on the next publish, and the warning names the key.
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
    carries exactly the file's value (nothing to decide), or the two differ only
    by leaves the release lacks (those are filled in, see `_fill_absent_leaves`).
    A key that still differs after that keeps the release's value and is named to
    the caller rather than dropped in silence.

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
            filled = _fill_absent_leaves(value, active_payload[key])
            merged[key] = filled
            if filled != value:
                unadopted.append(key)
        if key in known or merged[key] == value:
            recordable[key] = packaged_digests[key]
    for key, value in active_payload.items():
        merged.setdefault(key, value)
    return merged, tuple(sorted(unadopted)), recordable


def _require_secret_resolver(resolver: SecretResolver | None) -> SecretResolver:
    """Return the resolver, or refuse the AI paths that cannot run without one.

    Publishing a configuration release needs no secrets beyond the ones the
    process was already started with. Recording AI provider routes does: every
    provider credential is held as a `vault://` reference and only a resolver can
    turn one into a key. So the resolver is required here, at the point of use,
    rather than for the command as a whole -- which is what lets the ordinary
    `--if-missing` publish run on a stack with `PLATFORM_VAULT_ENABLED=false`.
    """

    if resolver is None:
        raise RuntimeError(
            "AI provider validation and route refresh resolve provider credentials "
            "from Vault, so they cannot run while PLATFORM_VAULT_ENABLED is false. "
            "Publish without --validate-ai/--refresh-ai-routes, or enable Vault."
        )
    return resolver


async def _prepare_return_configuration(
    *,
    validate_ai: bool,
    force_ai_validation: bool = False,
    refresh_ai_routes: bool = False,
    settings: Settings,
    resolver: SecretResolver | None,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
    existing_configuration: ReturnPlatformConfiguration | None = None,
) -> ReturnPlatformConfiguration:
    if refresh_ai_routes:
        print("ai_bootstrap_validation=SKIPPED reason=receipt-and-configuration-refresh")
        return await build_configured_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
            existing_configuration=existing_configuration,
        )

    if not validate_ai:
        print("ai_bootstrap_validation=SKIPPED reason=explicit-parameter-required")
        return configuration
    if validate_ai and not isinstance(settings, Settings):
        return await build_bootstrap_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
        )

    decision = await begin_ai_validation_run(
        settings=settings,
        force=force_ai_validation,
    )
    if not decision.allowed:
        print(
            "ai_bootstrap_validation=SKIPPED "
            f"reason={decision.reason} "
            f"run_id={decision.run_id or 'none'}"
        )
        return await build_configured_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
            existing_configuration=existing_configuration,
        )

    if decision.run_id is None:
        raise RuntimeError("Allowed AI validation run is missing a run ID")

    try:
        prepared = await build_bootstrap_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
        )
    except Exception as exc:
        await finish_ai_validation_run(
            settings=settings,
            run_id=decision.run_id,
            status="FAILED",
            error_type=type(exc).__name__,
        )
        raise

    await finish_ai_validation_run(
        settings=settings,
        run_id=decision.run_id,
        status="COMPLETED",
    )
    return prepared


async def main(
    *,
    if_missing: bool = False,
    validate_ai: bool = False,
    force_ai_validation: bool = False,
    refresh_ai_routes: bool = False,
    adopt_packaged: bool = False,
    adopt_packaged_keys: tuple[str, ...] = (),
) -> None:
    settings, resolver = await resolve_runtime_settings_from_vault(
        Settings(),
        resolve_ai_credentials=False,
    )
    # `resolve_runtime_settings_from_vault` returns no resolver for exactly one
    # reason: `PLATFORM_VAULT_ENABLED` is false, so the process was started with
    # its credentials already in the environment rather than behind references.
    # That is now the default and the only configuration this repository ships.
    #
    # Refusing outright would be wrong even so. This command is what
    # `runtime-configuration-init` runs, and every application service waits on
    # that init completing, so a refusal takes the whole profile down with it.
    #
    # Publishing needs no resolver: the release is built from the packaged YAML
    # and the active release's own payload. Only the AI validation paths do, and
    # `_require_secret_resolver` refuses those individually.
    if resolver is None:
        print("vault_secret_resolver=DISABLED reason=PLATFORM_VAULT_ENABLED-false")

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_user,
            settings.neo4j_password.get_secret_value(),
        ),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        active = await repository.get_active_release()

        if if_missing and active is not None and not validate_ai and not refresh_ai_routes:
            print(f"graph_configuration_release={active.release_id}")
            print("graph_configuration_status=EXISTING")
            print("ai_bootstrap_validation=SKIPPED reason=active-release-reused")
            return

        loaded = load_return_configuration(settings.return_configuration_path)
        loaded_ai_gateway = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
        loaded_dependency_simulation = load_dependency_simulation_configuration(
            settings.dependency_simulation_configuration_path
        )

        # The baseline the publish may truthfully record, per top-level key.
        #
        # The whole file by default because the ordinary case is the honest one:
        # with no active release, or none carrying this domain, the published
        # payload IS the packaged file. Where a payload is carried forward,
        # `_carry_forward` narrows this to the keys it could decide: a key whose
        # published value still holds something the file has since changed is
        # left out, because stamping the current file over it would mark that
        # dropped change as a deliberate edit and freeze it out for good.
        packaged_payload = loaded.configuration.model_dump(mode="json")
        recordable_baseline: dict[str, str] = _key_digests(packaged_payload)
        adopt_requests = _adopt_requests(adopt_packaged_keys)
        existing_configuration: ReturnPlatformConfiguration | None = None
        if active is not None:
            active_payload = await repository.get_domain_config(
                active.release_id,
                RETURN_PLATFORM_DOMAIN_KEY,
            )
            if active_payload is not None:
                # Carrying the active release's operator values forward is the
                # point of this block -- but only while that release still
                # validates. Unguarded, this was a deadlock: a release published
                # before a schema change (a removed key, or one that became
                # required) fails validation here, so no NEW release can be
                # published, so the stale release stays active. The only tool
                # that could repair it was blocked by the thing it repairs.
                #
                # Observed exactly that way: the active release predated both the
                # removal of `agents.*.failure_policy` and the addition of the
                # now-required `return_policy.bol_tendering_instruction_types`,
                # and failed with seven validation errors. Every process
                # independently rejected the same release and fell back to the
                # version-controlled baseline, so adoption reported ACTIVATING
                # forever with nothing able to move it.
                #
                # Falling back to the packaged YAML is the recoverable answer,
                # and it is loud rather than silent: the operator values in that
                # release ARE being dropped, which is a real loss and must be
                # read, not discovered later.
                #
                # The packaged document underneath is the second half of the
                # same problem, and it was missing. A key added to
                # `config/returns/production.yaml` after the active release was
                # cut is simply not in `active_payload`, so validating that
                # payload alone produced a configuration holding the *model*
                # default for the new key -- and republishing wrote that default
                # back. The new setting could never reach a deployment that had
                # ever published a release, which is every deployment.
                #
                # Observed with `copilot.order_discovery_agent_id`: the value was
                # in the YAML, the endpoint that serves it was correct, and
                # `/api/runtime-config` still answered `null`.
                #
                # A top-level merge, at the same granularity the rest of this
                # function works at. Keys the release predates come from the
                # packaged file; keys an operator edited stay as the operator left
                # them. Which of the two a disagreement IS gets decided against
                # the baseline the release recorded when it was published -- see
                # `_carry_forward`, and `PACKAGED_KEY_DIGESTS` for why a merge
                # without one cannot decide it at all.
                #
                # Before that baseline existed this was `{**packaged, **active}`,
                # and the second half of the intent above was never delivered: a
                # key the release carried always won, so nothing INSIDE a
                # top-level key could ever be changed by editing the packaged
                # file. `discovery` is one key, so an identification field added
                # to `config/returns/production.yaml` reached no deployment that
                # had ever published a release -- which is every deployment after
                # its first boot. The run said `UNCHANGED` and nothing else.
                baseline = active.metadata.get(PACKAGED_KEY_DIGESTS)

                merged_payload, unadopted, recordable_baseline = _carry_forward(
                    packaged_payload,
                    active_payload,
                    baseline,
                )
                # The operator's answer to an undecidable key, and the only
                # thing here that can overwrite an operator's own edits -- which
                # is why it is a flag and not a default. Per key so that taking
                # the file for `discovery` does not also take it for the
                # `policy_evaluation` an operator switched on.
                adopted_keys = set(adopt_requests.get(RETURN_PLATFORM_DOMAIN_KEY, ()))
                if adopt_packaged:
                    adopted_keys.update(packaged_payload)
                unknown_keys = sorted(adopted_keys - set(packaged_payload))
                if unknown_keys:
                    raise ValueError(
                        "adopt-packaged-key names keys the packaged configuration "
                        f"does not have: {', '.join(unknown_keys)}"
                    )
                packaged_digests = _key_digests(packaged_payload)
                for key in adopted_keys:
                    merged_payload[key] = packaged_payload[key]
                    recordable_baseline[key] = packaged_digests[key]
                unadopted = tuple(key for key in unadopted if key not in adopted_keys)
                if unadopted:
                    logger.warning(
                        "packaged_configuration_not_adopted release_id=%s keys=%s; "
                        "these keys have no recorded baseline and the release and "
                        "the packaged file disagree inside them, so an operator's "
                        "edit and a change to the file cannot be told apart and "
                        "the release wins (leaves the release lacks were filled "
                        "from the file). Every other key now carries a baseline "
                        "and decides itself from here on. Re-run with "
                        "--adopt-packaged-key <key> to take the packaged file for "
                        "one of these keys, or --adopt-packaged for all of them.",
                        active.release_id,
                        ",".join(unadopted),
                    )
                try:
                    existing_configuration = ReturnPlatformConfiguration.model_validate(
                        merged_payload
                    )
                except ValidationError as error:
                    logger.error(
                        "active_release_no_longer_validates release_id=%s errors=%d; "
                        "falling back to the packaged configuration. Operator values "
                        "carried by that release are NOT preserved -- re-apply them "
                        "after this publish. Detail: %s",
                        active.release_id,
                        error.error_count(),
                        error,
                    )

        # The other two domains, carried forward the same way. Each is decided
        # per unit against the baseline recorded for that domain, and validated
        # as a whole afterwards: a merge that no longer validates falls back to
        # the packaged file for that domain, loudly, as the business domain does.
        packaged_domains: dict[str, dict[str, Any]] = {
            AI_GATEWAY_DOMAIN_KEY: loaded_ai_gateway.configuration.model_dump(mode="json"),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: (
                loaded_dependency_simulation.configuration.model_dump(mode="json")
            ),
        }
        domain_models: dict[str, type[AIGatewayConfiguration | DependencySimulationConfiguration]]
        domain_models = {
            AI_GATEWAY_DOMAIN_KEY: AIGatewayConfiguration,
            DEPENDENCY_SIMULATION_DOMAIN_KEY: DependencySimulationConfiguration,
        }
        carried_domains: dict[str, dict[str, Any]] = dict(packaged_domains)
        recordable_domain_baselines: dict[str, dict[str, str]] = {}
        domain_baselines: Mapping[str, Any] = (
            (active.metadata.get(PACKAGED_DOMAIN_KEY_DIGESTS) or {}) if active is not None else {}
        )
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
            active_domain = (
                await repository.get_domain_config(active.release_id, domain_key)
                if active is not None
                else None
            )
            if active_domain is None:
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
                merged_domain = (
                    domain_models[domain_key].model_validate(merged_domain).model_dump(mode="json")
                )
            except ValidationError as error:
                logger.error(
                    "active_domain_no_longer_validates release_id=%s domain=%s errors=%d; "
                    "falling back to the packaged configuration for this domain. "
                    "Operator values carried by that release are NOT preserved -- "
                    "re-apply them after this publish. Detail: %s",
                    active.release_id if active is not None else "",
                    domain_key,
                    error.error_count(),
                    error,
                )
                continue
            carried_domains[domain_key] = merged_domain
            recordable_domain_baselines[domain_key] = recordable
            if unadopted_units:
                logger.warning(
                    "packaged_configuration_not_adopted release_id=%s domain=%s keys=%s; "
                    "these units have no recorded baseline and the release and the "
                    "packaged file disagree inside them, so an operator's edit and a "
                    "change to the file cannot be told apart and the release wins. "
                    "Re-run with --adopt-packaged-key %s/<unit> to take the packaged "
                    "file for one of them.",
                    active.release_id if active is not None else "",
                    domain_key,
                    ",".join(unadopted_units),
                    domain_key,
                )

        base_configuration = existing_configuration or loaded.configuration
        configuration = await _prepare_return_configuration(
            validate_ai=validate_ai,
            force_ai_validation=force_ai_validation,
            refresh_ai_routes=refresh_ai_routes,
            settings=settings,
            resolver=resolver,
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=base_configuration,
            existing_configuration=existing_configuration,
        )

        baseline_payload = configuration.model_dump(mode="json")
        domain_payloads = {
            RETURN_PLATFORM_DOMAIN_KEY: baseline_payload,
            AI_GATEWAY_DOMAIN_KEY: carried_domains[AI_GATEWAY_DOMAIN_KEY],
            DEPENDENCY_SIMULATION_DOMAIN_KEY: carried_domains[DEPENDENCY_SIMULATION_DOMAIN_KEY],
        }
        release_metadata = {
            PACKAGED_KEY_DIGESTS: recordable_baseline,
            PACKAGED_DOMAIN_KEY_DIGESTS: recordable_domain_baselines,
        }
        payload_checksum = hashlib.sha256(
            json.dumps(
                domain_payloads,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        base_release_id = f"return-platform-{payload_checksum[:16]}"
        release_id = base_release_id

        if active is not None:
            active_payloads = await repository.get_all_domain_configs(active.release_id)
            if active_payloads == domain_payloads:
                # Nothing to publish, but the baseline still moves: the packaged
                # file can change and leave the merged payload identical -- the
                # key it changed was one an operator had already edited, so the
                # release keeps its value. Recording what the file says NOW is
                # what keeps that key readable as an operator edit next time
                # instead of drifting back into "changed by someone, unknown".
                if recordable_baseline:
                    await repository.set_release_metadata(active.release_id, release_metadata)
                print(f"graph_configuration_release={active.release_id}")
                print("graph_configuration_status=UNCHANGED")
                return

        existing = await repository.get_release(release_id)
        if existing is not None and existing.status in {"SUPERSEDED", "ARCHIVED"}:
            revision = await repository.get_head_revision() + 1
            while True:
                release_id = f"{base_release_id}-r{revision}"
                existing = await repository.get_release(release_id)
                if existing is None or existing.status not in {"SUPERSEDED", "ARCHIVED"}:
                    break
                revision += 1

        if existing is None or existing.status == "DRAFT":
            for domain_key, domain_payload in domain_payloads.items():
                await repository.save_draft_domain(
                    release_id,
                    domain_key,
                    domain_payload,
                    actor_id="linux-runtime-bootstrap",
                )
            existing = await repository.get_release(release_id)

        if existing is not None and existing.status == "DRAFT":
            await repository.promote_release(
                release_id,
                "VALIDATED",
                actor_id="linux-runtime-bootstrap",
            )
            existing = await repository.get_release(release_id)

        if existing is None:
            raise RuntimeError(f"Configuration release {release_id} was not created")

        if existing.status == "VALIDATED":
            await repository.promote_release(
                release_id,
                "RELEASED",
                actor_id="linux-runtime-bootstrap",
                expected_head_revision=(await repository.get_head_revision()),
            )
        elif existing.status != "RELEASED":
            raise RuntimeError(
                f"Existing graph configuration release {release_id} has status {existing.status}"
            )

        # The baseline this release was built from, recorded on the release
        # itself so the NEXT publish can tell an operator's edit apart from a
        # change to the packaged file. Digests of the PACKAGED values, never of
        # the published ones: what is published also carries state this function
        # generates -- AI receipts under `runtime_integrations` -- and recording
        # those as the baseline would mark them unedited and let the next run
        # overwrite them from the file.
        #
        # After the release, because metadata sits outside the checksum that is
        # frozen at VALIDATED. A publish that reached RELEASED and failed here
        # leaves a correct release with no baseline, which is the recoverable
        # direction: the next run decides nothing and says so.
        if recordable_baseline:
            await repository.set_release_metadata(release_id, release_metadata)

        print(f"graph_configuration_release={release_id}")
        print("graph_configuration_status=READY")
    finally:
        await driver.close()


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help=("Publish bootstrap configuration only when no active release exists."),
    )
    parser.add_argument(
        "--validate-ai",
        action="store_true",
        help=("Run live provider/model validation when the daily validation interval has elapsed."),
    )
    parser.add_argument(
        "--force-ai-validation",
        action="store_true",
        help=("Bypass the daily validation interval. This is an operator-only action."),
    )
    parser.add_argument(
        "--refresh-ai-routes",
        action="store_true",
        help=(
            "Publish all configured provider/model/credential routes without calling AI providers."
        ),
    )
    parser.add_argument(
        "--adopt-packaged",
        action="store_true",
        help=(
            "Take the packaged configuration file for every key it declares, "
            "overwriting the active release. Needed only for a release published "
            "before releases recorded a packaged baseline: without one, a publish "
            "cannot tell an operator's edit from a change to the file and keeps the "
            "release. This and --adopt-packaged-key are the only paths that can "
            "overwrite an operator's edits; prefer the per-key form."
        ),
    )
    parser.add_argument(
        "--adopt-packaged-key",
        action="append",
        default=[],
        metavar="KEY",
        help=(
            "Take the packaged configuration file for ONE unit (repeatable), "
            "overwriting the active release for that unit only: a business key "
            "such as `discovery`, or `AI_GATEWAY/tasks.<TASK_ID>`, "
            "`DEPENDENCY_SIMULATION/dependencies.<NAME>`. "
            "The narrow form of --adopt-packaged: answers a "
            "packaged_configuration_not_adopted warning for the key it names "
            "without touching any key an operator edited."
        ),
    )
    args = parser.parse_args()

    if args.force_ai_validation:
        args.validate_ai = True
    if args.refresh_ai_routes and args.validate_ai:
        parser.error("--refresh-ai-routes cannot be combined with AI validation")

    asyncio.run(
        main(
            if_missing=args.if_missing,
            validate_ai=args.validate_ai,
            force_ai_validation=args.force_ai_validation,
            refresh_ai_routes=args.refresh_ai_routes,
            adopt_packaged=args.adopt_packaged,
            adopt_packaged_keys=tuple(args.adopt_packaged_key),
        )
    )


if __name__ == "__main__":
    run()
