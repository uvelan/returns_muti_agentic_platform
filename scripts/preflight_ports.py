"""Refuse to start when a published port and the port the client dials disagree.

This class of failure has now cost two debugging sessions, on two datastores, in
two directions:

  * SQL Server published 11433 while `PLATFORM_SQLSERVER_PORT` said 14330, and
    every host process died on "SQL Server did not become reachable" against a
    container that was healthy and listening.
  * Temporal published 17233 while `PLATFORM_TEMPORAL_TARGET` said
    `localhost:7233`, and every process died on "tcp connect error
    127.0.0.1:7233 Connection refused" -- with `docker ps` printing
    `127.0.0.1:17233->7233/tcp` two lines above the traceback.

Both times the evidence was on screen and meant nothing, because a published
port and a dialled port are two facts that nothing compared. This compares them.

It reads what Compose resolves (so it accounts for `.env`, shell exports and
defaults) and what the application settings resolve to, and reports every
disagreement at once rather than one reconnect at a time.

Exit codes: 0 agree, 1 disagree, 2 could not check.

    python scripts/preflight_ports.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))


def _compose_published() -> dict[str, set[int]]:
    """Host ports Compose will publish, per service."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["docker", "compose", "config", "--format", "json"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed: {result.stderr.strip()[:200]}")
    document = json.loads(result.stdout)
    published: dict[str, set[int]] = {}
    for name, service in (document.get("services") or {}).items():
        ports = set()
        for entry in service.get("ports") or []:
            value = entry.get("published") if isinstance(entry, dict) else None
            if value is None:
                continue
            try:
                ports.add(int(value))
            except (TypeError, ValueError):
                continue
        if ports:
            published[name] = ports
    return published


def _port_of(value: str | None, default: int | None = None) -> int | None:
    """The port in a `host:port`, a URI, or a bare number."""
    if not value:
        return default
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    if "://" in text:
        parsed = urlsplit(text)
        return parsed.port or default
    match = re.search(r":(\d+)\s*$", text)
    return int(match.group(1)) if match else default


def _dialled() -> dict[str, tuple[str, int | None]]:
    """What the application will actually connect to, per service.

    Read through `Settings` rather than off the `.env` text, so the answer
    accounts for defaults, shell overrides and any parsing the settings do --
    which is what the processes themselves will see.
    """
    from return_platform.configuration.settings import Settings  # noqa: PLC0415

    settings = Settings()  # type: ignore[call-arg]
    mongo = settings.mongo_dsn.get_secret_value() if settings.mongo_dsn else None
    return {
        "sqlserver": ("PLATFORM_SQLSERVER_PORT", _port_of(str(settings.sqlserver_port))),
        "temporal": ("PLATFORM_TEMPORAL_TARGET", _port_of(str(settings.temporal_target))),
        "neo4j": ("PLATFORM_NEO4J_URI", _port_of(str(settings.neo4j_uri))),
        "mongodb": ("PLATFORM_MONGO_DSN", _port_of(mongo)),
    }


def main() -> int:
    try:
        published = _compose_published()
        dialled = _dialled()
    except Exception as exc:  # noqa: BLE001 - a broken check is not a broken stack
        print(f"[preflight-ports] could not check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    problems: list[str] = []
    for service, (variable, port) in sorted(dialled.items()):
        exposed = published.get(service)
        if not exposed:
            # Not published is legitimate -- a service reached only from inside
            # the compose network, or one this deployment does not run.
            continue
        if port is None:
            problems.append(f"{service}: {variable} names no port; cannot compare with {exposed}")
            continue
        if port in exposed:
            print(f"[preflight-ports] {service:<12} {port}  agrees with {variable}")
            continue
        problems.append(
            f"{service}: compose publishes {sorted(exposed)} but {variable} dials {port}. "
            f"The container will be healthy and unreachable."
        )

    if problems:
        print("\n[preflight-ports] MISMATCH -- the stack cannot work as configured:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\n  Set them to the same value. On Windows a port can be inside a WinNAT\n"
            "  reserved range -- check `netsh interface ipv4 show excludedportrange\n"
            "  protocol=tcp` and move both sides together.",
            file=sys.stderr,
        )
        return 1

    print("[preflight-ports] OK: every published port matches the port its client dials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
