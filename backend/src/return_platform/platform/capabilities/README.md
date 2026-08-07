# platform/capabilities

Cross-module access without cross-module imports.

## The model

A provider publishes a concrete object; a consumer resolves the shape it needs.
Neither imports the other — `bootstrap/adapters/` is the only package that ever
imports two modules, to wire a provider's native contract into a consumer's port.

```python
# ai/module.py -- the AI module publishes its own native contract
registry.publish(CapabilityName.AI_INVOCATION, AiGatewayContract, "ai", gateway)

# bootstrap/adapters/agent_ai_adapter.py -- binds it to one consumer shape
registry.publish(CapabilityName.AI_INVOCATION, AgentAiPort, "bootstrap",
                  AgentAiAdapter(gateway))

# bootstrap/adapters/analyzer_ai_adapter.py -- binds it to a DIFFERENT consumer shape
registry.publish(CapabilityName.AI_INVOCATION, SchemaReasoningPort, "bootstrap",
                  AnalyzerAiAdapter(gateway))

# graph_schema_analyzer/module.py -- resolves its OWN shape; imports nothing from ai
self._reasoning = capabilities.resolve(CapabilityName.AI_INVOCATION, SchemaReasoningPort)
```

## Registrations are keyed by (capability, contract), not by capability alone

This is the one thing to get right when adding a publication. `AI_INVOCATION` must
serve both `AgentAiPort.invoke(...)` and `SchemaReasoningPort.reason(...)`; keying on
the capability name alone makes those mutually exclusive — the second `publish()`
would raise `DuplicateCapability`, and whichever consumer lost would get a structural
mismatch at resolve time instead. Keying on the pair lets one provider back many
differently-shaped consumers. See `tests/platform/test_capability_keying.py` for the
concrete scenario this corrects.

## Conformance is checked in three layers — publication alone is not enough

`publish()` performs an `isinstance()` check against `contract`, which proves the named
methods *exist*. It does **not** verify parameter names, arity, types, or return
types — an adapter with `reason(self, prompt)` against a port declaring
`reason(self, task_id, context)` passes publication and fails at first call.

| Layer | Catches |
|---|---|
| `publish()` (`isinstance`) | missing/misnamed methods, wrong object entirely |
| mypy, in the phase gate | wrong parameters, arity, types, returns |
| a contract test for the adapter | wrong behaviour with a correct signature |

Make the static layer real by giving every adapter a typed factory function — the
return annotation is what mypy actually checks:

```python
def build_analyzer_ai_adapter(gateway: AiGatewayContract) -> SchemaReasoningPort:
    return AnalyzerAiAdapter(gateway)   # mypy proves this satisfies the port
```

A bare `publish(..., AnalyzerAiAdapter(gateway))` with no typed factory silently
downgrades conformance to attribute-existence only — treat it as a review defect.

## mypy and Protocol classes as `contract`

Every call to `publish`/`resolve`/`resolve_optional` passes a Protocol class as
`contract`. mypy's `type-abstract` check flags that as "only a concrete class can be
given" because Protocols cannot be instantiated — but this registry never instantiates
`contract`, only `isinstance()`s against it, so the check is a false positive for this
pattern. This is a known mypy limitation with Protocol-keyed registries, not a defect
here. Suppress it narrowly at each call site:

```python
registry.publish(  # type: ignore[type-abstract]
    CapabilityName.AI_INVOCATION, SchemaReasoningPort, "bootstrap", AnalyzerAiAdapter()
)
```

Every `bootstrap/adapters/*.py` factory will need this. Do not disable `type-abstract`
project-wide — that would also hide the cases where it is a real bug elsewhere.

## Errors

- `DuplicateCapability` — the same `(capability, contract)` pair was published twice.
- `CapabilityTypeMismatch` — the published instance fails the `isinstance` check.
- `CapabilityNotPublished` — `resolve()` found no publisher for the pair. Use
  `resolve_optional()` when the capability is genuinely optional for the caller.
