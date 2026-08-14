"""The analyzer's entire outward surface.

Every dependency on anything outside this module is declared here as a Protocol
and resolved from the capability registry at `resolve_capabilities()` time. There
is deliberately **no `adapters/` package** in this module: binding these ports to
their real implementations (`configuration.sources.registry`, the AI gateway, the
graph lifecycle) happens in `bootstrap/adapters/`, which is the only place
allowed to see both sides. `tests/graph_schema_analyzer/test_independence.py` and
`tests/platform/test_no_module_cross_imports.py` enforce that statically.

The point is not ceremony. It is that the analyzer can be reasoned about, tested,
and changed without loading the AI gateway or the graph module at all -- and that
a future change to either cannot reach in here except through a named contract.
"""

from __future__ import annotations

from return_platform.graph_schema_analyzer.ports.audit_port import AnalyzerAuditPort
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.masking_port import (
    PayloadRedactionPort,
    RedactionPolicyFactory,
    SampleMaskerFactory,
    SampleMaskingPort,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    DiscoveredDataset,
    SourceDiscoveryPort,
)
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort

__all__ = [
    "AnalyzerAuditPort",
    "DiscoveredDataset",
    "GraphTargetPort",
    "PayloadRedactionPort",
    "PersistencePort",
    "RedactionPolicyFactory",
    "SampleMaskerFactory",
    "SampleMaskingPort",
    "SourceDiscoveryPort",
]
