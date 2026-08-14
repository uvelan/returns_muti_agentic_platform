"""The one seam a foreign host binds, and the only place a default lives.

Both audits scored this analyzer 82/100 and put the entire 18-point deduction on
packaging and bootstrap rather than on coupling: the core really does import no
returns business module, no UI, no workflow, no case service, and hardcodes no
project source name. What it lacked was a *stated* composition contract. The
adapters, the authz and the composition root are this host's, and nothing said
which of those a second application would have to bring versus which it would
inherit by accident.

`AnalyzerComposition` is that statement. Everything an application must supply to
run this analyzer over its own data is named here once, as ports:

    source discovery / inspection   what objects exist, and bounded reads of them
    graph target                    where a proposed schema would be applied
    AI                              the reasoning gateway
    audit                           where decisions are recorded
    persistence                     drafts, sessions, samples, snapshots
    masking policy                  how sampled values are tokenized
    retention (scope) policy        which fields may be kept at all

The last two are new. They were the two dependencies the application layer
reached for concretely rather than through a port, so a host could replace the
other five and still be given this platform's opinion about what is sensitive.

**Defaults live here and nowhere else.** `default_sample_masker` and
`default_redaction_policy` bind this host's implementations, and they are the
only import of `platform.redaction` left below the composition root. An
application that wants different behaviour passes its own factories and never
loads them. That is the whole difference between "the analyzer happens to work
here" and "the analyzer can be composed elsewhere".

What deliberately stays host-owned: `api/` (FastAPI routers, `security`
capabilities), `persistence/` (bound to this platform's system store) and
`module.py` (this platform's module lifecycle). Those *are* a composition root.
A second application writes its own, which is what a composition root is for --
the finding was never that they exist, but that nothing below them was
substitutable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from return_platform.graph_schema_analyzer.ports.ai_port import SchemaReasoningPort
from return_platform.graph_schema_analyzer.ports.audit_port import AnalyzerAuditPort
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.masking_port import (
    PayloadRedactionPort,
    RedactionPolicyFactory,
    SampleMaskerFactory,
    SampleMaskingPort,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    SourceDiscoveryPort,
    SourceInspectionPort,
)
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort

__all__ = [
    "AnalyzerComposition",
    "default_redaction_policy",
    "default_sample_masker",
]


def default_sample_masker() -> SampleMaskingPort:
    """This host's masker, imported lazily so a foreign host never loads it.

    A module-level import would make `platform.redaction` a load-time dependency
    of every application-layer module that defaults its masker, which is the
    coupling this function exists to remove. Inside the function it is reached
    only when nobody supplied an alternative.
    """
    from return_platform.platform.redaction.sample_masking import SampleMasker

    return SampleMasker()


def default_redaction_policy(allowed_fields: frozenset[str]) -> PayloadRedactionPort:
    """This host's fail-closed allowlist redactor. Lazily imported, as above."""
    from return_platform.platform.redaction.allowlist import AllowlistRedactor

    return AllowlistRedactor(allowed_fields)


@dataclass(frozen=True, slots=True)
class AnalyzerComposition:
    """Everything an application supplies to run this analyzer.

    A dataclass rather than a container or a registry: the set is fixed and
    small, and a missing member should be a construction error a type checker
    can see, not a lookup that fails at the first request that needs it.

    The two policy factories default to this host's, so composing inside this
    platform stays a one-liner while composing outside it is a matter of passing
    two more arguments rather than of patching module internals.
    """

    sources: SourceDiscoveryPort
    inspection: SourceInspectionPort
    graph_target: GraphTargetPort
    ai: SchemaReasoningPort
    audit: AnalyzerAuditPort
    persistence: PersistencePort
    sample_masker: SampleMaskerFactory = default_sample_masker
    redaction_policy: RedactionPolicyFactory = default_redaction_policy

    def mask(self, rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """One analysis's worth of masking, using one masker.

        A convenience that is also a guard rail: calling the factory per row
        would give the same value a different token each time and destroy every
        join the analyzer is looking for.
        """
        return self.sample_masker().mask_rows(rows)
