"""Deprecated import path. The AI Control Center now lives in `return_platform.ai`.

Phase 13 consolidated this package into `ai/`. Every module still present here is a
pure re-export of the canonical object -- same class, same identity, no wrapping --
so `isinstance`, `issubclass`, and pydantic model identity all behave as if the
caller had imported from `ai` directly.

**This layer exists only because the callers could not be moved in the same commit.**
Twenty modules outside the AI lane import `return_platform.ai_gateway`, including
`main.py` and `dynamic_knowledge/integration/runtime_factory.py`, which belong to the
integration owner and to the concurrent graph-lifecycle lane respectively. The sweep
that rewrites those imports and deletes this package is tracked in
`docs/execution/d1-d2-ai.md` as a coordination item.

Do not add anything here. New code imports from `return_platform.ai`.
"""
