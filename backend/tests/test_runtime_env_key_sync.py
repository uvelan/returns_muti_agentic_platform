import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast


def test_missing_keys_are_copied_from_example_without_overwriting_existing(
    tmp_path: Path,
) -> None:
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "linux"
        / "ensure_runtime_env_keys.py"
    )
    namespace: dict[str, Any] = runpy.run_path(str(script_path))
    update = cast(
        Callable[[Path, Path], tuple[str, ...]],
        namespace["update"],
    )

    example = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    example.write_text(
        "EXISTING=example\nMISSING_ONE=one\nMISSING_TWO=two\n",
        encoding="utf-8",
    )
    env_file.write_text("EXISTING=local\n", encoding="utf-8")

    changed = update(env_file, example)
    content = env_file.read_text(encoding="utf-8")

    assert "EXISTING=local" in content
    assert "EXISTING=example" not in content
    assert "MISSING_ONE=one" in content
    assert "MISSING_TWO=two" in content
    assert changed == ("MISSING_ONE", "MISSING_TWO")
