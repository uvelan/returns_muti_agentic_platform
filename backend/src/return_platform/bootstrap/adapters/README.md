# bootstrap/adapters

The only package in this codebase permitted to import two domain modules at once.

## Why it exists

Every other module resolves its dependencies through
`platform.capabilities.CapabilityRegistry`, never through a direct import of another
module. Something still has to bind a provider's native contract to a consumer's
specific port shape — that binding is a real cross-module dependency, and this package
is where it is allowed to live, instead of leaking into `graph_schema_analyzer/`,
`agents/`, or any other consumer.

```python
# bootstrap/adapters/analyzer_ai_adapter.py
from return_platform.ai.gateway.contracts import AiGatewayContract
from return_platform.graph_schema_analyzer.ports.ai_port import SchemaReasoningPort


class AnalyzerAiAdapter:
    def __init__(self, gateway: AiGatewayContract) -> None:
        self._gateway = gateway

    async def reason(self, task_id: str, context: object) -> object:
        return await self._gateway.invoke(task_id, context)


def build_analyzer_ai_adapter(gateway: AiGatewayContract) -> SchemaReasoningPort:
    return AnalyzerAiAdapter(gateway)
```

The typed factory (`build_analyzer_ai_adapter`) is not optional decoration — it is
what makes mypy check the adapter against the consumer's port. See
`platform/capabilities/README.md` for why `isinstance` alone is not enough.

## What's here now

The analyzer's four outward ports, bound to concrete providers:

| Port | Bound by |
|---|---|
| `SchemaReasoningPort` | `analyzer_ai_adapter.py` |
| `GraphTargetPort` | `analyzer_graph_target_adapter.py`, `analyzer_release_compiler.py` |
| `SourceDiscoveryPort` | `analyzer_source_adapter.py` |
| `SourceInspectionPort` | `source_inspection_{mongodb,sqlserver,postgresql,neo4j}.py`, routed by `source_inspection_routing.py`, statistics shared via `source_inspection_profiling.py` |

The `source_inspection_*` set is W4.5's read-only interface — `validate`, `list_sources`,
`list_objects`, `describe_object`, `sample`, `profile`, `list_indexes`,
`list_relationships` — one adapter per backend, each serving exactly one source.
They live here rather than in `source_connectors/` because they speak the analyzer's
port types directly, and `bootstrap/adapters/` is the only package permitted to see
both sides. Dispatch reuses `source_connectors.registry.SourceConnectorsByType`
rather than introducing a second registry.

**No adapter here may grow a write, DDL, or arbitrary-query method.** The ports they
implement have nowhere to put one, which is the control; adding one to a concrete
class would put it out of reach of the port and therefore out of reach of the scope
filter in `graph_schema_analyzer/application/source_inspection.py`.

## Rule

No module other than this one has an `adapters/` package. That includes
`graph_schema_analyzer/`, which is independent by design and must resolve its ports
entirely through the capability registry — see
`tests/platform/test_no_module_cross_imports.py`, which fails the build if a stray
`adapters/` directory appears anywhere else in the tree.
