"""Every host path that manages workers must manage the housekeeping one.

The reclaimers were all built, wired and configured, and none of them had ever
run. The worker that hosts them was the only one no host startup script asked
for: `09_start_workers.sh` and `run_all_host.ps1` each iterated a hardcoded list
of five, and `housekeeping` was not in it. `run_worker_host.sh` and its
PowerShell twin both *accept* the argument -- nothing ever passed it.

Nothing complained, and that is the part worth guarding. `housekeeping-worker` is
deliberately excluded from `REQUIRED_PROCESS_CLASSES`, so process adoption
reaches LIVE and `/health/ready` reports every dependency healthy with every
reclaimer dead. The only symptom is unbounded growth in stores nobody is
watching: interceptions that never expire, and graph generations that never
reclaim.

A list of process names repeated across five files is exactly the kind of thing
that loses an entry in a refactor, so this asserts the entry rather than trusting
it. It reads the scripts as text rather than running them -- the point is that
the name is present in each list, and a test that needed a host to prove it is a
test nobody runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Each path that starts, monitors, validates or stops the managed workers, and
#: the token that names the housekeeping one in that file's vocabulary.
#:
#: Start and stop are both here on purpose. A worker that starts and is never
#: stopped leaks a process across a restart, which on this stack means two
#: reclaimers racing the same leases.
_WORKER_MANAGEMENT_PATHS: dict[str, str] = {
    "scripts/linux/09_start_workers.sh": "housekeeping",
    "scripts/run_all_host.ps1": '"housekeeping"',
    "scripts/run_all_host.sh": "worker-housekeeping",
    "scripts/linux/11_validate_host_processes.sh": "worker-housekeeping",
    "scripts/linux/17_stop_host_processes.sh": "worker-housekeeping",
}


@pytest.mark.parametrize(("relative_path", "token"), sorted(_WORKER_MANAGEMENT_PATHS.items()))
def test_the_housekeeping_worker_is_named_in_every_worker_path(
    relative_path: str, token: str
) -> None:
    path = _REPO_ROOT / relative_path
    assert path.is_file(), f"{relative_path} has moved; update this test with it"

    assert token in path.read_text(encoding="utf-8"), (
        f"{relative_path} manages the host workers and does not name "
        f"{token}. Every reclaimer the platform has runs in that worker, and it "
        f"is excluded from REQUIRED_PROCESS_CLASSES -- so adoption reaches LIVE "
        f"and health stays green while nothing reclaims anything."
    )


def test_the_worker_runner_still_accepts_the_argument() -> None:
    """The startup lists are only useful if the runner takes the name.

    Both runners accepted `housekeeping` for as long as the lists omitted it,
    which is how the gap survived: every piece was present except the one line
    that connects them.
    """
    for relative_path in ("scripts/run_worker_host.sh", "scripts/run_worker_host.ps1"):
        source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "housekeeping" in source, f"{relative_path} no longer accepts housekeeping"
        assert "run_housekeeping_worker.py" in source, (
            f"{relative_path} names no housekeeping entrypoint"
        )
