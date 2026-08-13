"""Carries an approved `CONFIGURATION` proposal into a configuration release.

Binds the proposal kernel (platform) to the configuration module's release
lifecycle. `bootstrap/adapters/` is the only place permitted to see both.

**A release, not a file.** The whole of W4.2 is here: the edited agent module is
written into a new `AGENT_MODULES` domain on a fresh release cloned from the
active one, the release is validated and published through the single promotion
path, and `RuntimeConfigurationActivator` picks it up. Nothing writes packaged
YAML (execution rule 11) and every replica sees the change because every replica
reads the same release.

**The whole domain, not the edited module.** A release is an immutable document
set; publishing only the module that changed would drop every other agent's
configuration from it, and the next process to boot would find seven agents
missing. `released_documents()` returns the effective document for each agent --
the release's where it has one, the packaged seed where it does not -- and this
overwrites exactly one entry in that set.
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
from return_platform.configuration.snapshot import AGENT_MODULES_DOMAIN_KEY
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
        # The candidate is re-validated here, against the loader, at the moment
        # it would become real. The submission-time check ran against the
        # configuration as it stood then; an approval can sit in the queue for
        # days, and a module the packaged manifest no longer declares must not
        # be published because it was valid last week.
        try:
            self._agents.validate_candidate(proposal.subject_id, dict(document))
        except ValueError as exc:
            raise ActivationRefused(str(exc)) from exc

        modules = self._agents.released_documents()
        modules[proposal.subject_id] = dict(document)

        release_id = f"agent-config-{_UNSAFE_ID.sub('-', proposal.proposal_id)}"
        settings = self._resources.settings
        try:
            outcome = await publish_release_with_domains(
                repository=self._repository,
                release_id=release_id,
                domains={AGENT_MODULES_DOMAIN_KEY: modules},
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
