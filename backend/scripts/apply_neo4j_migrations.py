#!/usr/bin/env python3
"""Container entry point for Neo4j migrations."""

from return_platform.configuration.cli.apply_neo4j_migrations import run

if __name__ == "__main__":
    run()
