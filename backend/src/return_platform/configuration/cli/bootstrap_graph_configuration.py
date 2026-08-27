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


def _key_digests(payload: Mapping[str, Any]) -> dict[str, str]:
    """One digest per top-level key, over its canonical JSON."""
    return {
        key: hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for key, value in payload.items()
    }


def _carry_forward(
    packaged: Mapping[str, Any],
    active_payload: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Combine the packaged file with the active release, and say what it dropped.

    Returns the merged payload and the keys the packaged file changed that could
    NOT be adopted -- empty whenever the answer is exact.

    With a baseline the decision is per key and needs no judgement: a release
    value that still matches what the packaged file said when the release was cut
    was never edited, so the file's new value replaces it; a value that has moved
    away from that baseline was changed by someone after the file was read -- an
    operator through the config API, or this bootstrap writing AI receipts into
    `runtime_integrations` -- and is kept.

    Without a baseline nothing can be decided, so nothing is: the release wins,
    exactly as before, and the keys that lost are named to the caller rather than
    dropped in silence. A release published by this function records a baseline,
    so a deployment needs the undecidable path at most once.
    """
    if baseline is None:
        merged = {**packaged, **active_payload}
        unadopted = tuple(
            sorted(
                key
                for key, value in packaged.items()
                if key in active_payload and active_payload[key] != value
            )
        )
        return merged, unadopted

    active_digests = _key_digests(active_payload)
    merged = dict(active_payload)
    for key, value in packaged.items():
        if key not in active_payload or active_digests[key] == baseline.get(key):
            merged[key] = value
    return merged, ()


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

        # Whether what gets published is a truthful baseline for the packaged
        # file -- which is what gates recording one at all.
        #
        # True by default because the ordinary case is the honest one: with no
        # active release, or none carrying this domain, the published payload IS
        # the packaged file. It goes false only where a payload is carried
        # forward that this run could not decide about, because the published
        # payload then still holds values the file has since changed, and
        # stamping the current file over them would mark those dropped changes as
        # deliberate edits and freeze them out for good.
        baseline_decidable = True
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
                baseline_decidable = baseline is not None or adopt_packaged

                merged_payload, unadopted = _carry_forward(
                    loaded.configuration.model_dump(mode="json"),
                    active_payload,
                    baseline,
                )
                if unadopted:
                    logger.warning(
                        "packaged_configuration_not_adopted release_id=%s keys=%s; "
                        "this release predates the packaged baseline, so an edit to "
                        "the release and an edit to the packaged file cannot be told "
                        "apart and the release wins. Re-run with --adopt-packaged to "
                        "take the packaged file for these keys; the release it "
                        "publishes records a baseline and this cannot recur.",
                        active.release_id,
                        ",".join(unadopted),
                    )
                if adopt_packaged:
                    # The operator's answer to the paragraph above, and the only
                    # thing here that can overwrite an operator's own edits --
                    # which is why it is a flag and not a default.
                    merged_payload = {
                        **active_payload,
                        **loaded.configuration.model_dump(mode="json"),
                    }
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
            AI_GATEWAY_DOMAIN_KEY: (loaded_ai_gateway.configuration.model_dump(mode="json")),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: (
                loaded_dependency_simulation.configuration.model_dump(mode="json")
            ),
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
                if baseline_decidable:
                    await repository.set_release_metadata(
                        active.release_id,
                        {
                            PACKAGED_KEY_DIGESTS: _key_digests(
                                loaded.configuration.model_dump(mode="json")
                            )
                        },
                    )
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
        if baseline_decidable:
            await repository.set_release_metadata(
                release_id,
                {PACKAGED_KEY_DIGESTS: _key_digests(loaded.configuration.model_dump(mode="json"))},
            )

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
            "release. This is the only path that can overwrite an operator's edits."
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
        )
    )


if __name__ == "__main__":
    run()
