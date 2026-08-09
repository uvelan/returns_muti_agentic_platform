"""AI Control Center — the platform's single AI execution path.

See `README.md` for the architecture. Nothing here is re-exported at package level
on purpose: `ai.routing.tasks` (configuration) is imported by modules that
`ai.routing.selection` in turn imports, so a package-level convenience import would
close that cycle at import time.
"""
