"""Copy a packaged configuration directory so a test can edit one part file.

CFG-2 splits `backend/config/returns/production.yaml` into a directory of
part files. Tests that used to build an *edited single file* in `tmp_path`
(`test_window_policy_is_configuration.py`) now need an edited *directory*
instead -- `copied_configuration_tree` copies the whole tree so the edit
lands on one part file without disturbing the packaged original.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def copied_configuration_tree(source_dir: Path, dest: Path) -> Path:
    """Copy `source_dir` to `dest` and return `dest`.

    `dest` must not already exist (mirrors `shutil.copytree`'s own rule) --
    callers typically pass a fresh `tmp_path` subdirectory.
    """
    shutil.copytree(source_dir, dest)
    return dest
