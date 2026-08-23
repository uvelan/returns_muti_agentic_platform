"""The SQL migrations, as batches a probe database can be built from.

Every module that needs a throwaway SQL schema used to carry its own copy of
this: a hand-written tuple of migration filenames, plus the same twenty lines
of read, strip `USE`, split on `GO`. Four copies, four subsets, maintained by
whoever remembered.

They were not remembered. `007_return_record_method.sql` and
`008_return_record_carrier.sql` added `return_method` and `carrier` to
`dbo.return_record`; two of the four lists were never updated, and eleven
live-infrastructure tests failed with `Invalid column name`. The probe database
was a version of the schema that has not existed since those migrations landed.

So the default is **every migration, in order** -- the same thing
`configuration/cli/apply_sql_migrations.py` does to a real database. A new
migration is picked up by existing probes without anyone editing a list, which
is the only version of this that stays true.

Ordering is by filename, which is numeric because the names are zero-padded.
"""

from __future__ import annotations

import re
from importlib.resources import files

_MIGRATION_PACKAGE = "configuration/sql_migrations"

#: `USE [database]` on its own line, removed so the DDL lands in the throwaway
#: database rather than the application's own. Stripped by *line*, not by
#: dropping the batch containing it: these files open with a comment block, so
#: `USE` shares a batch with that comment and a batch-level filter lets it
#: through -- which pointed every CREATE at `return_platform` and failed the
#: suite on `Invalid object name 'dbo.return_case'`.
_USE_STATEMENT = re.compile(r"^\s*USE\s+\[?[A-Za-z0-9_]+\]?\s*;?\s*$", re.IGNORECASE | re.MULTILINE)
_BATCH_SEPARATOR = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def all_migrations() -> tuple[str, ...]:
    """Every migration filename, in the order they must be applied."""
    directory = files("return_platform").joinpath(_MIGRATION_PACKAGE)
    return tuple(
        sorted(entry.name for entry in directory.iterdir() if entry.name.endswith(".sql"))
    )


def migration_batches(migrations: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """The batches to execute, in order. Defaults to the whole schema."""
    batches: list[str] = []
    for migration in migrations if migrations is not None else all_migrations():
        text = (
            files("return_platform")
            .joinpath(_MIGRATION_PACKAGE)
            .joinpath(migration)
            .read_text(encoding="utf-8")
        )
        batches.extend(
            batch.strip() for batch in _BATCH_SEPARATOR.split(_USE_STATEMENT.sub("", text)) if batch.strip()
        )
    return tuple(batches)
