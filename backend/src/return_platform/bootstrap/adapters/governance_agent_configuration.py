"""Carries an approved `CONFIGURATION` proposal into a configuration release.

Binds the proposal kernel (platform) to the configuration module's release
lifecycle. `bootstrap/adapters/` is the only place permitted to see both.

**A release, not a file (W4.2), patching one key of one domain (CFG-5b).**
The edited agent document is written into `agents.<id>` on a fresh clone of
the active release's `RETURN_PLATFORM` domain, the release is validated and
published through the single promotion path, and
`RuntimeConfigurationActivator` picks it up. Nothing writes packaged YAML
(execution rule 11) and every replica sees the change because every replica
reads the same release.

**The whole `RETURN_PLATFORM` document, not the edited agent alone.**
`publish_release_with_domains` replaces a domain whole
(`merged.update({key: dict(value) ...})`), so publishing only `{"agents":
{id: doc}}` under `RETURN_PLATFORM_DOMAIN_KEY` would silently delete
`discovery`, `return_policy`, every other section of the domain from the next
release. `released_return_platform_document()` returns the *whole* current
`RETURN_PLATFORM` document; this overwrites exactly one entry inside its
`agents` mapping and publishes the result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from return_platform.configuration.application.agent_configuration import (
    AGENT_MODULE_KEY_ROOT,
    AgentConfigurationService,
)
from return_platform.configuration.application.release_promotion import (
    ReleasePromotionError,
    publish_release_with_domains,
)
from return_platform.configuration.graph_repository import ConfigurationGraphRepository
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY
from return_platform.platform.governance.errors import ActivationRefused
from return_platform.platform.governance.ports import ActivationReceipt
from return_platform.platform.governance.proposal import Proposal
from return_platform.resources import RuntimeResources

__all__ = ["AgentConfigurationProposalActivator"]

#: Release ids are constrained by `CreateReleasePayload` to
#: `^[A-Za-z0-9][A-Za-z0-9_.:-]+$`. A proposal id is a UUID behind a `proposal-`
#: prefix, which already satisfies that, but the derived id is sanitised anyway
#: rather than trusted -- an id that fails the pattern would surface as an
#: unexplained rejection three layers down.
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]")


class AgentConfigurationProposalActivator:
    def __init__(
        self,
        *,
        agents: AgentConfigurationService,
        repository: ConfigurationGraphRepository,
        resources: RuntimeResources,
        activator: RuntimeConfigurationActivator,
    ) -> None:
        self._agents = agents
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
        document = proposal.after.get(AGENT_MODULE_KEY_ROOT)
        if not isinstance(document, Mapping):
            raise ActivationRefused(
                f"proposal {proposal.proposal_id} carries no agent module document under "
                f"{AGENT_MODULE_KEY_ROOT!r}."
            )
        # The candidate is re-validated here, against `AgentConfiguration`, at
        # the moment it would become real. The submission-time check ran
        # against the model as it stood then; an approval can sit in the queue
        # for days, and a document a newer release of the model would refuse
        # must not be published because it was valid last week.
        try:
            _receipt, canonical = self._agents.validate_candidate(
                proposal.subject_id, dict(document)
            )
        except ValueError as exc:
            raise ActivationRefused(str(exc)) from exc

        return_platform_document = self._agents.released_return_platform_document()
        agents = dict(return_platform_document.get("agents", {}))
        agents[proposal.subject_id] = canonical
        return_platform_document["agents"] = agents

        release_id = f"agent-config-{_UNSAFE_ID.sub('-', proposal.proposal_id)}"
        settings = self._resources.settings
        try:
            outcome = await publish_release_with_domains(
                repository=self._repository,
                release_id=release_id,
                domains={RETURN_PLATFORM_DOMAIN_KEY: return_platform_document},
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
