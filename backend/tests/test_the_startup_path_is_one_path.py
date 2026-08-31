"""One entrypoint, one port, and a lock that works where the repo says it does.

Three declarations of "how to start the backend" disagreed. `.claude/launch.json`
and the README table named `return_platform.main:create_app --factory`;
`run_backend_host.sh` and `run_backend_host.ps1` named `return_platform.asgi:app`.
Both work, which is why nobody noticed and why it cost the audit time to
reconcile: nothing was broken, there was just no answer to "which one is it".

`BACKEND_PORT` had the same shape. `.env` set it, the README documented it, and
every script hardcoded `8000` -- so setting it moved nothing.

And `prepare_runtime_configuration.sh` refused to run without `flock`, which Git
Bash on Windows does not ship, while `bootstrap_host.ps1`, `run_backend_host.ps1`
and all of `scripts/windows/` advertise Windows support. `run_worker_host.sh`
even carries a comment handling "Windows under Git Bash" for the venv path,
inside a script that could not start there.

These are text assertions over the scripts because that is where the drift
lives. A runtime test would prove one path works; the defect was that there were
three and they disagreed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: The single supported ASGI entrypoint.
#:
#: `asgi:app` over `main:create_app --factory` because three of the four
#: declarations already used it and it needs no extra flag. `asgi.py` is two
#: lines and calls the factory, so the factory is still the only place an app is
#: built.
ENTRYPOINT = "return_platform.asgi:app"

#: Every file that says how to start the backend.
_DECLARING_FILES: tuple[Path, ...] = (
    _ROOT / ".claude" / "launch.json",
    _ROOT / "scripts" / "run_backend_host.sh",
    _ROOT / "scripts" / "run_backend_host.ps1",
    _ROOT / "README.md",
)


@pytest.mark.parametrize("path", _DECLARING_FILES, ids=lambda path: path.name)
def test_no_file_declares_the_other_entrypoint(path: Path) -> None:
    """`main:create_app` may be imported; it may not be advertised as the way in."""
    assert path.is_file(), f"{path} has moved; update this test with it"
    text = path.read_text(encoding="utf-8", errors="replace")

    stale = "return_platform.main:create_app"
    assert stale not in text, (
        f"{path.name} still advertises {stale}. There is one entrypoint, "
        f"{ENTRYPOINT}, and a second name that also works is what made this "
        f"cost half an hour to reconcile."
    )


def test_the_launch_configuration_starts_the_one_entrypoint() -> None:
    configuration = json.loads((_ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    backend = next(entry for entry in configuration["configurations"] if entry["name"] == "backend")
    assert ENTRYPOINT in backend["runtimeArgs"]
    # `--factory` belongs to the other form. Left behind it would be passed to
    # uvicorn alongside a module that is already an app.
    assert "--factory" not in backend["runtimeArgs"]


@pytest.mark.parametrize(
    ("script", "pattern"),
    [
        ("run_backend_host.sh", r'--port\s+"\$BACKEND_PORT"'),
        ("run_backend_host.ps1", r"--port \$BackendPort"),
    ],
)
def test_the_host_scripts_use_the_configured_port(script: str, pattern: str) -> None:
    """`.env` sets `BACKEND_PORT`; the scripts have to read it for that to mean anything."""
    text = (_ROOT / "scripts" / script).read_text(encoding="utf-8", errors="replace")

    assert re.search(pattern, text), f"{script} does not pass the configured port"
    assert not re.search(r"--port\s+8000\b", text), (
        f"{script} still hardcodes 8000, so BACKEND_PORT is decoration"
    )


class TestPreparationLocking:
    """The lock has to exist on every host that runs the script that takes it."""

    @property
    def _script(self) -> str:
        return (_ROOT / "scripts" / "prepare_runtime_configuration.sh").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_a_missing_flock_is_no_longer_fatal(self) -> None:
        assert "flock is required" not in self._script, (
            "the script still aborts without flock, which Git Bash on Windows "
            "does not ship -- and run_backend_host.sh and run_worker_host.sh "
            "both call this script"
        )

    def test_flock_is_still_preferred_where_it_exists(self) -> None:
        """The kernel releases its lock when the holder dies. Nothing else does."""
        script = self._script
        assert "command -v flock" in script
        assert "flock 9" in script

    def test_the_fallback_records_its_holder(self) -> None:
        """Otherwise a crashed run leaves a directory that blocks every later one."""
        script = self._script
        assert "LOCK_DIR" in script
        assert 'kill -0 "$holder"' in script, "a stale lock cannot be detected without this"
        assert "trap release_lock_dir EXIT" in script, "the lock must be released on exit"

    def test_the_fallback_gives_up_rather_than_hanging(self) -> None:
        script = self._script
        assert "PLATFORM_PREPARE_LOCK_WAIT_SECONDS" in script
        assert "Timed out after" in script


def test_the_windows_escape_hatch_is_documented() -> None:
    """The audit found the workaround by reading the script, which is not documentation."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8", errors="replace")

    assert "PLATFORM_SKIP_RUNTIME_PREPARE" in readme
    assert "PLATFORM_PREPARE_LOCK_WAIT_SECONDS" in readme


def test_the_env_acl_refusal_names_its_remedy() -> None:
    """The rule stays; the dead end does not.

    The POSIX branch of this check ends with "run chmod 600 .env". The Windows
    branch ended with the rule and nothing else, so an operator meeting it had a
    refusal and no supported way forward -- which is exactly where the audit
    stopped, correctly declining to change a security setting it had not been
    asked to change.
    """
    source = (_ROOT / "scripts" / "linux" / "validate_env.py").read_text(encoding="utf-8")

    assert "icacls" in source
    assert "/inheritance:r" in source
    # And the rule itself is unchanged: still only these three principals.
    assert '"builtin\\\\administrators"' in source
    assert '"nt authority\\\\system"' in source
