"""Definition of done: no command writes agent configuration (bytes or mtimes)."""

import os
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.paths import resolve
from tests.audit import guarded


def snapshot(roots: list[Path], exclude: list[Path]) -> dict[str, tuple[bytes, int]]:
    state = {}
    for root in roots:
        for path in root.rglob("*"):
            if any(path == e or e in path.parents for e in exclude):
                continue
            if path.is_file() and not path.is_symlink():
                state[str(path)] = (path.read_bytes(), os.stat(path).st_mtime_ns)
    return state


COMMANDS = [
    ["--version"],
    ["init", "--non-interactive", "--force"],
    ["inventory"],
    ["inventory", "--json"],
    ["inventory", "--no-cache"],
    ["usage"],
    ["usage", "--json", "--since", "2026-01-01"],
    ["report", "--format", "html"],
    ["report", "--format", "json"],
    ["doctor"],
    ["validate"],
]


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: " ".join(c))
def test_command_leaves_agent_config_untouched(
    harnesses: Path, tmp_path: Path, command: list[str]
) -> None:
    paths = resolve()
    exclude = [paths.kit_config_dir, paths.kit_cache_dir]
    roots = [tmp_path]
    before = snapshot(roots, exclude)
    assert before, "fixture should populate the sandbox"
    with guarded(exclude) as violations:
        try:
            main(command)
        except SystemExit:
            pass
    assert violations == [], violations
    assert snapshot(roots, exclude) == before


def test_guard_catches_writes_and_subprocesses(tmp_path: Path) -> None:
    import subprocess
    import sys

    with guarded([tmp_path / "ok"]) as violations:
        (tmp_path / "bad.txt").write_text("x")
        subprocess.run([sys.executable, "-c", "pass"], check=False)
    assert any("bad.txt" in v for v in violations)
    assert any("subprocess" in v or "posix_spawn" in v for v in violations)
