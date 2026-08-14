"""The published contract, checked by the suite that is supposed to notice.

`scripts/check_openapi_drift.py` existed and was not wired into pytest, so the
one question it answers -- does the document we ship still match the code --
was asked only when somebody remembered to ask it. It caught real drift the
green suite could not see: GRAPH-02 left five committed artifacts stale while
every test passed.

A test rather than only a script, because "run this other command as well" is
not a gate. The script keeps its job: it is the *writer* (`--write`
regenerates every snapshot) and it emits the evidence receipt. This is the
reader, and it imports the same generator rather than reimplementing it, so
the two cannot disagree about what the document is.

THE TYPESCRIPT STAGE, HONESTLY
------------------------------
Four of the five artifacts are JSON and are compared here with no toolchain at
all. The fifth is `frontend/src/api/generated/return-platform.d.ts`, which only
`openapi-typescript` can produce, which lives in `frontend/node_modules`, which
a git worktree does not have -- the script reports `TS_GEN_UNAVAILABLE` there.

That stage therefore **skips with a reason naming what is missing**, and does
not pass. The distinction is the whole point: a skip says "not checked here,
and here is why", which a reader can act on; asserting nothing and reporting
green would say "checked, and fine", which is false in exactly the environment
where it is most likely to be false. Where `node_modules` exists -- CI, and any
full checkout that has run `npm install` -- the check runs for real.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from export_openapi import build_openapi_bytes  # noqa: E402 - path set above

#: Every committed copy of the document, exactly as the drift script lists them.
#: They are duplicates of one artifact and each one is somebody's source of
#: truth, so while they exist they must agree.
JSON_SNAPSHOTS = (
    REPOSITORY_ROOT / "openapi" / "return-platform.openapi.json",
    REPOSITORY_ROOT / "backend" / "openapi" / "return-platform.openapi.json",
    REPOSITORY_ROOT / "frontend" / "openapi" / "return-platform.openapi.json",
    REPOSITORY_ROOT / "openapi.json",
)

COMMITTED_TYPES = (
    REPOSITORY_ROOT / "frontend" / "src" / "api" / "generated" / "return-platform.d.ts"
)

TYPES_GENERATOR = (
    REPOSITORY_ROOT / "frontend" / "node_modules" / "openapi-typescript" / "bin" / "cli.js"
)

_REGENERATE = "python scripts/check_openapi_drift.py --write"


@pytest.fixture(scope="module")
def generated() -> bytes:
    """The document the current code produces.

    Built once for the module: `create_app()` mounts every router, and doing it
    per test would be the slowest thing in the default suite for no gain.
    """
    return build_openapi_bytes()


@pytest.mark.parametrize(
    "snapshot",
    JSON_SNAPSHOTS,
    ids=[p.relative_to(REPOSITORY_ROOT).as_posix() for p in JSON_SNAPSHOTS],
)
def test_every_committed_openapi_snapshot_matches_the_code(
    snapshot: Path, generated: bytes
) -> None:
    """Byte-for-byte, because the serialization is canonical.

    `build_openapi_bytes` sorts keys and fixes the indent precisely so that a
    formatting difference cannot read as a contract change, which means a
    difference here is a real one.

    Parametrized per file rather than collecting a list of diffs: when the
    frontend copy is stale and the backend copy is not, the failure should name
    the frontend copy.
    """
    relative = snapshot.relative_to(REPOSITORY_ROOT).as_posix()
    assert snapshot.is_file(), f"{relative} is missing; regenerate with `{_REGENERATE}`"
    assert snapshot.read_bytes() == generated, (
        f"{relative} no longer matches the document the code produces. "
        f"If the contract change is intended, regenerate with `{_REGENERATE}` "
        f"and commit the result."
    )


def test_the_committed_typescript_types_match_the_code(generated: bytes, tmp_path: Path) -> None:
    """The fifth artifact, when the toolchain that makes it is present.

    Skipped -- not passed -- when `openapi-typescript` is absent, which is the
    ordinary state of a git worktree. See the module docstring.
    """
    if not TYPES_GENERATOR.is_file():
        pytest.skip(
            f"openapi-typescript is not installed at {TYPES_GENERATOR.relative_to(REPOSITORY_ROOT).as_posix()}; "
            "the .d.ts contract is NOT checked in this environment. "
            "Run `npm install` in frontend/ to enable it."
        )

    source = tmp_path / "return-platform.openapi.json"
    source.write_bytes(generated)
    destination = tmp_path / "return-platform.d.ts"
    result = subprocess.run(
        ["node", str(TYPES_GENERATOR), str(source), "--output", str(destination)],
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == 0 and destination.is_file(), (
        f"openapi-typescript failed ({result.returncode}): {result.stderr[-2000:]}"
    )

    def _normalized(text: str) -> str:
        """Line endings only -- the repository is worked on from Windows and
        Linux, and a CRLF difference is not a contract change."""
        return text.replace("\r\n", "\n")

    relative = COMMITTED_TYPES.relative_to(REPOSITORY_ROOT).as_posix()
    assert COMMITTED_TYPES.is_file(), f"{relative} is missing; regenerate with `{_REGENERATE}`"
    assert _normalized(COMMITTED_TYPES.read_text(encoding="utf-8")) == _normalized(
        destination.read_text(encoding="utf-8")
    ), (
        f"{relative} no longer matches the document the code produces. "
        f"Regenerate with `{_REGENERATE}` and commit the result."
    )


def test_the_drift_script_and_this_test_check_the_same_files() -> None:
    """Two readers of one contract must not drift from each other.

    The script is the writer and this is the gate. If somebody adds a fifth
    JSON snapshot to `check_openapi_drift.py` and not here, the suite would go
    on passing while an artifact nobody checks goes stale -- which is the exact
    failure this whole file exists to prevent, one level up.
    """
    script = (REPOSITORY_ROOT / "scripts" / "check_openapi_drift.py").read_text(encoding="utf-8")
    for snapshot in JSON_SNAPSHOTS:
        name = snapshot.relative_to(REPOSITORY_ROOT).as_posix()
        # The script builds its paths from `ROOT / ... / ...` fragments, so the
        # final component is what is greppable and stable between the two.
        assert snapshot.name in script, f"{name} is checked here but not by the drift script"
    assert COMMITTED_TYPES.name in script, (
        "the .d.ts artifact is checked here but not by the drift script"
    )
    # The reverse direction: a snapshot the script writes and this file does not
    # read is an artifact regenerated by nobody's gate.
    assert script.count("ROOT / ") >= len(JSON_SNAPSHOTS), (
        "the drift script appears to manage more snapshots than this test reads"
    )
