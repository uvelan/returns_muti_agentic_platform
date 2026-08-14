"""Reclaiming the operational debris three subsystems leave behind.

Debris accumulates and degrades the platform measurably. On this deployment: 208
orphaned Temporal executions holding task queues and making a real-infrastructure
suite fail on a different test each run; 212 `GraphGeneration` markers, each with
a full projection of the source data behind it, because retirement is status-only
by design; and a growing set of `*_probe` SQL Server databases created by test
fixtures that clean rows and not databases.

Each debris class has its own reclaimer, and each reclaimer decides eligibility
with a **positive** test -- a statement about why a resource *is* reclaimable,
never a heuristic about whether it looks stale. `temporal_executions.py` carries
the longest explanation of why, because the naive rule there ("terminate anything
running for more than N hours") would terminate live return cases mid-wait, and
the rule that replaces it has to be safe by construction rather than by
configuration.

`cycle.py` composes them; `scripts/run_housekeeping_worker.py` is the deployed
process. The reclaimers themselves take no Settings and no configuration object,
only resolved values -- so the rules can be exercised without a database, a
Temporal server or a release.
"""
