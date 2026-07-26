#!/usr/bin/env python3
"""Verify that every mandatory exact route is declared in the frontend router."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("routes_source", type=Path)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    route_entries = catalog.get("routes")
    if not isinstance(route_entries, list) or len(route_entries) != 23:
        parser.error("mandatory route catalog must contain exactly 23 routes")

    routes: list[str] = []
    for entry in route_entries:
        if not isinstance(entry, dict):
            parser.error("mandatory route catalog entries must be objects")
        route = entry.get("route")
        dynamic = entry.get("dynamic")
        if not isinstance(route, str) or not route.startswith("/") or not isinstance(dynamic, bool):
            parser.error("mandatory route catalog contains an invalid route")
        routes.append(route)
    if len(set(routes)) != 23:
        parser.error("mandatory route catalog contains duplicate routes")

    source = args.routes_source.read_text(encoding="utf-8")
    missing = sorted(route for route in routes if route not in source)
    if missing:
        parser.error("frontend router is missing: " + ", ".join(missing))

    print("Verified all 23 mandatory routes in the frontend router.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
