#!/usr/bin/env python3
"""Apply ordered Neo4j migrations and verify discovery indexes."""

from return_platform.configuration.cli.apply_neo4j_migrations import run


if __name__ == "__main__":
    run()
