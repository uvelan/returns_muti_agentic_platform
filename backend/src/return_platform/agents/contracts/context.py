"""AgentExecutionContext: everything an agent's execute() may reach for.

Deliberately platform-neutral -- no `.ai`, no `.knowledge`, no other agent, no domain
type. An agent declares the narrower shapes it actually needs in
agents/contracts/ports.py (or its own package's ports.py, for a port only it uses) and
resolves them from `capabilities` at execution time; it never imports another module
directly. Getting this right now matters: a context with named module fields would
force a second refactor once LangGraph and Temporal orchestration land on top of it.

`configuration` is a RuntimeConfigurationView (a pinned window onto exactly one
release), never a RuntimeConfigurationHandle -- an agent execution is always scoped to
the release the calling session was pinned to when it started, even if a newer release
is activated while it runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.contracts.audit import AuditSink
from return_platform.platform.contracts.clock import Clock
from return_platform.platform.contracts.consistency import ConsistencyHandle
from return_platform.platform.contracts.redaction import Redactor
from return_platform.platform.contracts.runtime_configuration import RuntimeConfigurationView
from return_platform.security.principal import Principal


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    configuration: RuntimeConfigurationView
    capabilities: CapabilityRegistry
    audit: AuditSink
    redactor: Redactor
    principal: Principal
    correlation_id: str
    session_id: str
    configuration_release_id: str
    clock: Clock
    consistency: ConsistencyHandle | None = None
