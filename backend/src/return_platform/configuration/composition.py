"""Compose a packaged configuration directory into one document.

`return_configuration.py` and `ai/routing/tasks.py` each load one packaged
YAML file today. A directory is the same idea split into per-function files:
an `index.yaml` names the document-level keys, an ordered `parts:` list of
whole-section files, and an optional `entries:` mapping of *section name* to
an ordered list of files whose own top-level content becomes one entry each
(the AI Gateway's `tasks`, keyed by filename stem).

This module holds one composer used by both loaders. A directory that fails
any rule below raises `ValueError` naming every offending file so the error
is actionable without a debugger.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: `entries` keys (task ids, etc.) must look like this. Deliberately close to
#: a POSIX filename: letters/digits first, then letters/digits/`_`/`.`/`-`.
#: A dotted id (`support.message.classify.v1`) is allowed by design (`.` is
#: also the Windows extension separator, so `Path.stem` only strips the last
#: one) -- the filesystem-safety check that allows this lives in the CFG-2
#: design note, not here.
_ENTRY_STEM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_MAX_TOTAL_BYTES = 1_000_000


@dataclass(frozen=True)
class ComposedDocument:
    """The result of composing one packaged configuration directory."""

    document: dict[str, Any]
    files: tuple[Path, ...]
    sha256: str
    total_bytes: int


def _load_yaml_mapping(path: Path, *, index_path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    parsed: Any = yaml.safe_load(raw)
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{path} is not a mapping at its root; every part of {index_path} must be a YAML object"
        )
    return parsed


def compose_configuration_document(
    directory: Path,
    *,
    document_keys: frozenset[str],
) -> ComposedDocument:
    """Compose the packaged configuration tree rooted at `directory`.

    `document_keys` names the keys `index.yaml` carries directly (for the
    return configuration: `schema_version`, `assumption_set_version`; for the
    AI Gateway: `schemaVersion`, `domain`, plus the cross-task sections that
    live inline in that index -- any key `index.yaml` declares outside
    `parts`/`entries` is accepted as a document key, so `document_keys` only
    has to name the keys that are *required*, not every inline section).

    **No `ignore` parameter.** CFG-2 carried one here for exactly one window --
    `backend/config/returns/production.yaml`, the packaged monolith the split
    replaced, sharing this directory with the part files that replaced it
    between the split commit and the later commit that deleted it. That
    deletion landed (CFG-2 step:06); the window it existed for is over, and a
    directory that scans clean without it is the point -- a `production.yaml`
    re-added by mistake, a bad merge, or a stray editor save is exactly the
    unlisted-file case the no-globbing scan below exists to catch, and a
    permanent `ignore` would have made it invisible again.
    """
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory")

    index_path = directory / "index.yaml"
    if not index_path.is_file():
        raise ValueError(f"configuration directory {directory} has no index.yaml")

    index = _load_yaml_mapping(index_path, index_path=index_path)

    missing_document_keys = sorted(document_keys - index.keys())
    if missing_document_keys:
        raise ValueError(
            f"{index_path} is missing document key(s): {', '.join(missing_document_keys)}"
        )

    parts_field = index.get("parts", [])
    if not isinstance(parts_field, list) or not all(isinstance(p, str) for p in parts_field):
        raise ValueError(f"{index_path} 'parts' must be a list of relative paths")

    entries_field = index.get("entries", {})
    if not isinstance(entries_field, dict):
        raise ValueError(f"{index_path} 'entries' must be a mapping of section to file list")

    document: dict[str, Any] = {}
    declared_by: dict[str, str] = {}
    files: list[Path] = [index_path]

    # 1. Inline document-level sections (everything index.yaml declares other
    #    than 'parts' and 'entries').
    for key, value in index.items():
        if key in ("parts", "entries"):
            continue
        document[key] = value
        declared_by[key] = str(index_path)

    # 2. Whole-section part files, in list order.
    for rel in parts_field:
        part_path = _resolve_listed_path(directory, rel, index_path=index_path)
        if not part_path.is_file():
            raise ValueError(f"{index_path} lists {rel}, which does not exist in {directory}")
        files.append(part_path)
        part_doc = _load_yaml_mapping(part_path, index_path=index_path)
        for key, value in part_doc.items():
            if key in declared_by:
                raise ValueError(
                    f"section {key} is declared in both {declared_by[key]} and {part_path}"
                )
            document[key] = value
            declared_by[key] = str(part_path)

    # 3. Entries sections: section name -> ordered list of files, each file's
    #    root mapping becomes one entry keyed by the file's stem.
    for section, entry_list in entries_field.items():
        if not isinstance(entry_list, list) or not all(isinstance(p, str) for p in entry_list):
            raise ValueError(f"{index_path} 'entries.{section}' must be a list of relative paths")
        if section in declared_by:
            raise ValueError(
                f"section {section} is declared in both {declared_by[section]} "
                f"and entries.{section} in {index_path}"
            )
        section_map: dict[str, Any] = {}
        stems_seen: dict[str, str] = {}
        for rel in entry_list:
            entry_path = _resolve_listed_path(directory, rel, index_path=index_path)
            if not entry_path.is_file():
                raise ValueError(f"{index_path} lists {rel}, which does not exist in {directory}")
            files.append(entry_path)
            stem = entry_path.stem
            if not _ENTRY_STEM_PATTERN.match(stem):
                raise ValueError(
                    f"{entry_path} has a stem ({stem!r}) that does not match "
                    f"{_ENTRY_STEM_PATTERN.pattern!r}"
                )
            folded = stem.casefold()
            if folded in stems_seen:
                raise ValueError(
                    f"entries.{section} stems collide under casefold(): "
                    f"{stems_seen[folded]} and {entry_path}"
                )
            stems_seen[folded] = str(entry_path)
            section_map[stem] = _load_yaml_mapping(entry_path, index_path=index_path)
        document[section] = section_map
        declared_by[section] = f"entries.{section} in {index_path}"

    # 4. Every *.yaml/*.yml under the directory must be named by parts or an
    #    entries list (or be index.yaml itself) -- no globbing.
    listed = {p.resolve() for p in files}
    unlisted: list[Path] = []
    for candidate in sorted(directory.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in (".yaml", ".yml"):
            resolved_candidate = candidate.resolve()
            if resolved_candidate not in listed:
                unlisted.append(candidate)
    if unlisted:
        names = ", ".join(str(p) for p in unlisted)
        raise ValueError(f"{names} is not listed in {index_path}; this loader does not glob")

    total_bytes = sum(f.stat().st_size for f in files)
    if total_bytes > _MAX_TOTAL_BYTES:
        raise ValueError(f"configuration directory {directory} exceeds 1 MB ({total_bytes} bytes)")

    digest = _framed_digest(index_path, files[1:])

    return ComposedDocument(
        document=document,
        files=tuple(files),
        sha256=digest,
        total_bytes=total_bytes,
    )


def _resolve_listed_path(directory: Path, rel: str, *, index_path: Path) -> Path:
    candidate = (directory / rel).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError as exc:
        raise ValueError(f"{index_path} lists {rel}, which escapes {directory}") from exc
    return candidate


def _framed_digest(index_path: Path, rest: list[Path]) -> str:
    """sha256 over `index.yaml` then every listed file, in list order.

    Each file is framed as `relpath\\0len\\0bytes` (relpath relative to the
    configuration directory) so the digest is unambiguous and order-sensitive,
    keeping today's single-file meaning: "the bytes on disk changed",
    comments included.
    """
    hasher = hashlib.sha256()
    directory = index_path.parent
    for path in [index_path, *rest]:
        raw = path.read_bytes()
        relpath = path.resolve().relative_to(directory.resolve()).as_posix()
        frame = f"{relpath}\0{len(raw)}\0".encode()
        hasher.update(frame)
        hasher.update(raw)
    return hasher.hexdigest()
