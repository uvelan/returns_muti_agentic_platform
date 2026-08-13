"""Carries an approved `IMPROVEMENT` proposal into the runtime configuration.

Binds the proposal kernel (platform) to the configuration release lifecycle,
which is the only way an improvement is allowed to take effect: plan section 7
says feedback proposals never activate anything directly, and this is what
"directly" excludes -- the change becomes a configuration release like any other,
validated as a whole document and picked up by the existing
`RuntimeConfigurationActivator`. Nothing here writes a setting into a running
process.

**The key table is the translation, and it is the same one the policy uses.**
`resolve_improvement_key` says both whether a key is permitted and where in
`ReturnPlatformConfiguration` it lands. Two tables -- one to police keys, one to
apply them -- would eventually permit a key nothing could apply, or apply a key
nothing had policed.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from return_platform.configuration.application.release_promotion import (
    ReleasePromotionError,
    publish_release_with_domains,
)
from return_platform.configuration.graph_repository import ConfigurationGraphRepository
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY
from return_platform.platform.governance.errors import ActivationRefused
from return_platform.platform.governance.key_policy import resolve_improvement_key
from return_platform.platform.governance.ports import ActivationReceipt
from return_platform.platform.governance.proposal import ChangeKind, Proposal
from return_platform.resources import RuntimeResources

__all__ = ["ImprovementProposalActivator", "apply_improvement_changes"]

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]")


def apply_improvement_changes(
    configuration: Mapping[str, Any], proposal: Proposal
) -> dict[str, Any]:
    """Return the configuration document with the proposal's changes applied.

    Works from the proposal's own diff rather than from its `after` document:
    the diff is what the reviewer approved, and the kernel has already re-derived
    it from before/after and refused the proposal if the two disagreed.
    """
    updated = copy.deepcopy(dict(configuration))
    for entry in proposal.diff:
        if entry.change is ChangeKind.REMOVED:
            raise ActivationRefused(
                f"improvement proposals cannot remove configuration; {entry.key} was dropped."
            )
        resolved = resolve_improvement_key(entry.key)
        if resolved is None:
            # Unreachable through the kernel, which refuses an unresolvable key
            # at submission and again at activation. Raised rather than skipped
            # so that a future key added to the allowlist without a document
            # path fails loudly instead of activating as a no-op.
            raise ActivationRefused(
                f"{entry.key} is permitted but bound to no configuration field."
            )
        permitted, collection_name = resolved
        _write(
            updated, permitted.document_path, collection_name, permitted.collection_key, entry.after
        )
    return updated


def _write(
    document: dict[str, Any],
    document_path: str,
    collection_name: str | None,
    collection_key: str | None,
    value: Any,
) -> None:
    segments = document_path.split(".")
    cursor: Any = document
    for segment in segments[:-1]:
        nested = cursor.get(segment)
        if not isinstance(nested, dict):
            raise ActivationRefused(
                f"the active configuration has no {'.'.join(segments)} to change."
            )
        cursor = nested
    leaf = segments[-1]

    if collection_name is None:
        cursor[leaf] = value
        return

    target = cursor.get(leaf)
    if collection_key == "":
        # A plain mapping addressed by its own key -- `discovery.anchor_weights`.
        if not isinstance(target, dict):
            raise ActivationRefused(
                f"{document_path} is not a mapping in the active configuration."
            )
        target[collection_name] = value
        return

    # A list of records addressed by one of their fields -- the smart questions,
    # addressed by `field`. Matched rather than indexed: the position of a
    # question in the list is not something anything promises to keep stable.
    if not isinstance(target, list):
        raise ActivationRefused(f"{document_path} is not a list in the active configuration.")
    for record in target:
        if isinstance(record, dict) and record.get(collection_key) == collection_name:
            record[_record_field(document_path)] = value
            return
    raise ActivationRefused(
        f"{document_path} has no entry whose {collection_key} is {collection_name!r}."
    )


def _record_field(document_path: str) -> str:
    """Which field of a matched record the value lands on.

    Only one keyed-list family exists (`clarification_policy.fields` addressed by
    `field`, setting `priority`), and naming it here keeps that fact in one
    place instead of encoding it in the key table's shape.
    """
    if document_path == "clarification_policy.fields":
        return "priority"
    raise ActivationRefused(f"{document_path} has no known record field to write.")


class ImprovementProposalActivator:
    def __init__(
        self,
        *,
        repository: ConfigurationGraphRepository,
        resources: RuntimeResources,
        activator: RuntimeConfigurationActivator,
    ) -> None:
        self._repository = repository
        self._resources = resources
        self._activator = activator

    async def activate(
        self,
        proposal: Proposal,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any],
    ) -> ActivationReceipt:
        del occurred_at, parameters
        active = await self._repository.get_active_release()
        if active is None:
            raise ActivationRefused(
                "there is no active configuration release to improve; bootstrap the "
                "configuration graph first."
            )
        current = await self._repository.get_domain_config(
            active.release_id, RETURN_PLATFORM_DOMAIN_KEY
        )
        if current is None:
            raise ActivationRefused(
                f"active release {active.release_id} carries no {RETURN_PLATFORM_DOMAIN_KEY} "
                "domain to improve."
            )
        updated = apply_improvement_changes(current, proposal)

        release_id = f"improvement-{_UNSAFE_ID.sub('-', proposal.proposal_id)}"
        settings = self._resources.settings
        try:
            outcome = await publish_release_with_domains(
                repository=self._repository,
                release_id=release_id,
                domains={RETURN_PLATFORM_DOMAIN_KEY: updated},
                actor_id=actor,
                mongo=self._resources.mongo,
                mongo_database=settings.mongo_database,
                activator=self._activator,
            )
        except ReleasePromotionError as exc:
            raise ActivationRefused(str(exc), reference=release_id) from exc

        return ActivationReceipt(
            reference=outcome.release.release_id,
            detail=(
                None
                if outcome.activated_snapshot is None
                else f"activated at head revision {outcome.activated_snapshot.head_revision}"
            ),
        )
