"""The composed packaged configuration directories equal the files they replace.

CFG-2 splits `backend/config/returns/production.yaml` and
`backend/config/ai_gateway.yaml` into directories of part files, composed at
load time by `configuration.composition`. This module proves the split lost
nothing:

- The two "one-shot" tests (`..._equals_the_file_it_was_split_from`) compare
  the composed directory against the exact pre-split bytes, fetched from git
  at `SPLIT_BASE_SHA` (falling back to a frozen copy when git cannot answer).
  They are retired the first time a part file is deliberately edited past the
  split itself -- CFG-6 deletes them once its own final PASS is in the
  ledger, per `.plan/tracks/CFG-2.design.md` sect. 3.
- `test_composing_the_directory_equals_loading_it_as_one_file` is the
  *durable* invariant: it never references the pre-split file and survives
  every future edit to a part file. It is what guards the composer from here
  on.
- The error tests exercise `compose_configuration_document`'s rules directly
  against small synthetic `tmp_path` fixtures.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from return_platform.ai.routing.tasks import load_ai_gateway_configuration
from return_platform.configuration.cli.bootstrap_graph_configuration import (
    CARRY_FORWARD_SPLIT_KEYS,
    _key_digests,
    _units,
)
from return_platform.configuration.composition import compose_configuration_document
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import BACKEND_ROOT, REPOSITORY_ROOT
from return_platform.configuration.snapshot import AI_GATEWAY_DOMAIN_KEY

#: The commit the packaged files were split from -- both still exist there as
#: single files, byte-identical to what `git show` returns today (no edit
#: has touched either file's content since). This is the CFG-2 lease's own
#: base sha (`.plan/tracks/CFG-2.brief.md`'s "Base" line), not the split
#: commit itself -- any commit between the two names the same bytes.
SPLIT_BASE_SHA = "73c276d231cd9e609678f8d29517d67cfb5d77a9"

RETURNS_DIR = BACKEND_ROOT / "config" / "returns"
AI_GATEWAY_DIR = BACKEND_ROOT / "config" / "ai_gateway"

FROZEN_RETURNS = Path(__file__).parent.parent / "data" / "pre_split" / "returns_production.yaml"
FROZEN_AI_GATEWAY = Path(__file__).parent.parent / "data" / "pre_split" / "ai_gateway.yaml"


def _pre_split_bytes(repo_path: str, frozen: Path) -> bytes:
    """Bytes at `SPLIT_BASE_SHA:repo_path`, or the frozen copy if git can't answer."""
    try:
        result = subprocess.run(
            ["git", "show", f"{SPLIT_BASE_SHA}:{repo_path}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozen.read_bytes()
    if result.returncode != 0 or not result.stdout:
        return frozen.read_bytes()
    return result.stdout


def test_the_composed_returns_document_equals_the_file_it_was_split_from(
    tmp_path: Path,
) -> None:
    """One-shot: retired by CFG-6 after its own final PASS is in the ledger."""
    raw = _pre_split_bytes("backend/config/returns/production.yaml", FROZEN_RETURNS)
    single_file = tmp_path / "production.yaml"
    single_file.write_bytes(raw)

    from_file = load_return_configuration(single_file)
    from_directory = load_return_configuration(RETURNS_DIR)

    assert from_directory.configuration.model_dump(
        mode="json"
    ) == from_file.configuration.model_dump(mode="json")


def test_the_composed_ai_gateway_document_equals_the_file_it_was_split_from(
    tmp_path: Path,
) -> None:
    """One-shot: retired by CFG-6 after its own final PASS is in the ledger."""
    raw = _pre_split_bytes("backend/config/ai_gateway.yaml", FROZEN_AI_GATEWAY)
    single_file = tmp_path / "ai_gateway.yaml"
    single_file.write_bytes(raw)

    from_file = load_ai_gateway_configuration(single_file)
    from_directory = load_ai_gateway_configuration(AI_GATEWAY_DIR)

    assert from_directory.configuration.model_dump(
        mode="json"
    ) == from_file.configuration.model_dump(mode="json")


def test_the_frozen_pre_split_copies_are_what_git_holds() -> None:
    """Byte equality against git -- skipped only when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "show", f"{SPLIT_BASE_SHA}:backend/config/returns/production.yaml"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available in this environment")
    if result.returncode != 0 or not result.stdout:
        pytest.skip("git could not answer for SPLIT_BASE_SHA (shallow or exported checkout)")

    assert result.stdout == FROZEN_RETURNS.read_bytes()

    gateway_result = subprocess.run(
        ["git", "show", f"{SPLIT_BASE_SHA}:backend/config/ai_gateway.yaml"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert gateway_result.returncode == 0
    assert gateway_result.stdout == FROZEN_AI_GATEWAY.read_bytes()


def test_the_packaged_key_digests_are_unchanged_by_the_split(tmp_path: Path) -> None:
    """The carry-forward proof stated directly: same per-key digests, same units."""
    raw = _pre_split_bytes("backend/config/ai_gateway.yaml", FROZEN_AI_GATEWAY)
    single_file = tmp_path / "ai_gateway.yaml"
    single_file.write_bytes(raw)

    from_file = load_ai_gateway_configuration(single_file).configuration.model_dump(mode="json")
    from_directory = load_ai_gateway_configuration(AI_GATEWAY_DIR).configuration.model_dump(
        mode="json"
    )

    assert _key_digests(from_file) == _key_digests(from_directory)

    split_keys = CARRY_FORWARD_SPLIT_KEYS[AI_GATEWAY_DOMAIN_KEY]
    file_units = _units(from_file, split_keys)
    directory_units = _units(from_directory, split_keys)
    task_units_from_file = {u for u in file_units if u.startswith("tasks.")}
    task_units_from_directory = {u for u in directory_units if u.startswith("tasks.")}
    assert task_units_from_file == task_units_from_directory
    assert len(task_units_from_directory) == 25


def test_composing_the_directory_equals_loading_it_as_one_file_returns(tmp_path: Path) -> None:
    """The durable invariant -- never references the pre-split file."""
    composed = compose_configuration_document(
        RETURNS_DIR,
        document_keys=frozenset({"schema_version", "assumption_set_version"}),
    )
    single_file = tmp_path / "composed.yaml"
    single_file.write_text(yaml.safe_dump(composed.document, sort_keys=False), encoding="utf-8")

    from_directory = load_return_configuration(RETURNS_DIR)
    from_composed_single_file = load_return_configuration(single_file)

    assert from_directory.configuration.model_dump(
        mode="json"
    ) == from_composed_single_file.configuration.model_dump(mode="json")


def test_composing_the_directory_equals_loading_it_as_one_file_ai_gateway(tmp_path: Path) -> None:
    """The durable invariant -- never references the pre-split file."""
    composed = compose_configuration_document(
        AI_GATEWAY_DIR,
        document_keys=frozenset(
            {
                "schemaVersion",
                "domain",
                "circuitBreaker",
                "retry",
                "rateLimits",
                "providerLimits",
                "modelContexts",
            }
        ),
    )
    single_file = tmp_path / "composed.yaml"
    single_file.write_text(yaml.safe_dump(composed.document, sort_keys=False), encoding="utf-8")

    from_directory = load_ai_gateway_configuration(AI_GATEWAY_DIR)
    from_composed_single_file = load_ai_gateway_configuration(single_file)

    assert from_directory.configuration.model_dump(
        mode="json"
    ) == from_composed_single_file.configuration.model_dump(mode="json")


def test_a_single_file_path_still_loads() -> None:
    """Today's behaviour, against the frozen pre-split copy -- unchanged."""
    loaded = load_return_configuration(FROZEN_RETURNS)
    assert loaded.path == FROZEN_RETURNS.resolve()
    assert loaded.configuration.schema_version == "1.0"

    gateway_loaded = load_ai_gateway_configuration(FROZEN_AI_GATEWAY)
    assert gateway_loaded.path == FROZEN_AI_GATEWAY.resolve()
    assert gateway_loaded.configuration.schemaVersion == "1.0"


# --- error tests -------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_a_section_declared_in_two_parts_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    _write(directory / "index.yaml", "parts:\n  - a.yaml\n  - b.yaml\n")
    _write(directory / "a.yaml", "shared: 1\n")
    _write(directory / "b.yaml", "shared: 2\n")

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(directory, document_keys=frozenset())

    message = str(excinfo.value)
    assert "a.yaml" in message
    assert "b.yaml" in message
    assert "shared" in message


def test_a_listed_part_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    _write(directory / "index.yaml", "parts:\n  - missing.yaml\n")

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(directory, document_keys=frozenset())

    message = str(excinfo.value)
    assert "missing.yaml" in message
    assert str(directory) in message


def test_a_file_present_but_unlisted_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    _write(directory / "index.yaml", "parts:\n  - a.yaml\n")
    _write(directory / "a.yaml", "known: 1\n")
    _write(directory / "stray.yaml", "unknown: 1\n")

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(directory, document_keys=frozenset())

    message = str(excinfo.value)
    assert "stray.yaml" in message
    assert "index.yaml" in message
    assert "does not glob" in message


def test_a_missing_index_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    directory.mkdir()

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(directory, document_keys=frozenset())

    message = str(excinfo.value)
    assert str(directory) in message
    assert "index.yaml" in message


def test_two_entry_stems_colliding_under_casefold_are_refused(tmp_path: Path) -> None:
    # Two genuinely distinct files (different suffix) whose *stems* collide
    # under casefold() -- same-suffix, different-case filenames collide as a
    # single file on a case-insensitive filesystem (NTFS) before this rule
    # ever runs, so the fixture has to differ by more than case to prove
    # anything there. The real collision this rule guards against is exactly
    # this shape: an AI Gateway task id and an existing one differing only in
    # case.
    directory = tmp_path / "cfg"
    _write(
        directory / "index.yaml",
        "entries:\n  tasks:\n    - tasks/Example.yaml\n    - tasks/EXAMPLE.yml\n",
    )
    _write(directory / "tasks/Example.yaml", "tier: STANDARD\n")
    _write(directory / "tasks/EXAMPLE.yml", "tier: LIGHTWEIGHT\n")

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(directory, document_keys=frozenset())

    message = str(excinfo.value)
    assert "Example.yaml" in message
    assert "EXAMPLE.yml" in message


# --- CFG-3a carry-overs from RV CFG-2 (F1, F2) --------------------------


def test_a_re_added_production_yaml_is_refused_as_an_unlisted_file(tmp_path: Path) -> None:
    """RV CFG-2 F1: the transitional `ignore={"production.yaml"}` this composer
    used to accept outlived the deletion commit and would have silently
    skipped a re-added file forever -- a bad merge, a stray editor save, or a
    revert that brings the monolith back alongside the part files that
    replaced it. CFG-3a removed the parameter; this proves the removal
    actually restores the no-globbing guarantee rather than merely deleting
    dead code.
    """
    directory = tmp_path / "returns"
    shutil.copytree(RETURNS_DIR, directory)
    # The exact file CFG-2 deleted, re-added by mistake. Its content is
    # irrelevant -- the composer must refuse it as unlisted before it ever
    # reads the YAML.
    (directory / "production.yaml").write_text("schema_version: '1.0'\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        compose_configuration_document(
            directory,
            document_keys=frozenset({"schema_version", "assumption_set_version"}),
        )

    message = str(excinfo.value)
    assert "production.yaml" in message
    assert "does not glob" in message


#: The 25 prompt-section names that were YAML anchors in the single-file
#: `backend/config/ai_gateway.yaml`, aliased (`*name`) by at least one other
#: task in that file -- i.e. genuinely *shared* text, not merely named.
#: Enumerated directly from `git show 73c276d2:backend/config/ai_gateway.yaml`
#: (the CFG-2 lease's own base sha, before the split): every `&name` anchor
#: with at least one `*name` alias elsewhere in the file.
FORMER_SHARED_ANCHOR_NAMES = (
    "role-and-untrusted-input",
    "action-payload-contract",
    "statement-and-identifier-rules",
    "when-to-search-instead-of-asking",
    "identity-before-order",
    "honouring-a-confirmation",
    "after-the-confirmation",
    "answering-about-the-return",
    "choosing-the-next-question",
    "measuring-with-aggregates",
    "offering-values-and-confirming",
    "graph-query-shape",
    "evidence-and-scope",
    "voice",
    "candidate-pages",
    "paging-the-cached-search",
    "search-intent-fields",
    "carrying-the-search-forward",
    "reporting-observed-facts",
    "naming-a-fact",
    "not-asking-twice",
    "source-system-escalation",
    "reading-the-transcript",
    "support-untrusted-input",
    "support-tone-and-disclosure",
)


def test_the_former_shared_anchor_prompt_blocks_have_not_drifted_apart(tmp_path: Path) -> None:
    """RV CFG-2 F2: one task per file ended YAML anchors crossing a file
    boundary, so each of the 25 blocks above is independently typed into
    every `ai_gateway/tasks/*.yaml` file that carries it -- with nothing left
    to stop the copies drifting the way a real anchor could not. This is the
    sync guard: every task that carries a given name must carry the *same*
    text, or the test names exactly which tasks disagree.
    """
    directory = tmp_path / "ai_gateway"
    shutil.copytree(AI_GATEWAY_DIR, directory)
    configuration = load_ai_gateway_configuration(directory).configuration

    carriers: dict[str, dict[str, str]] = {name: {} for name in FORMER_SHARED_ANCHOR_NAMES}
    for task_id, task in configuration.tasks.items():
        for section in task.systemPromptSections:
            if section.name in carriers:
                carriers[section.name][task_id] = section.text

    never_carried = sorted(name for name, tasks in carriers.items() if not tasks)
    assert not never_carried, (
        f"no task file carries former shared anchor(s): {never_carried} -- "
        "either the name was renamed in the split or this list is stale"
    )

    drifted = {
        name: sorted(tasks) for name, tasks in carriers.items() if len(set(tasks.values())) > 1
    }
    assert not drifted, f"former shared anchor(s) now disagree across tasks: {drifted}"
