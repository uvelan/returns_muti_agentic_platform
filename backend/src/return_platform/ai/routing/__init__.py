"""Task definitions, route construction, and route selection.

Deliberately empty of re-exports: `tasks` is imported by `routes` and `selection`,
so a package-level re-export would make `import return_platform.ai.routing.tasks`
pull in the whole subpackage and cycle.
"""
