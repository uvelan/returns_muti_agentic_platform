#!/usr/bin/env python3
"""Container entry point for SQL Server migrations."""

from return_platform.configuration.cli.apply_sql_migrations import run

if __name__ == "__main__":
    run()
