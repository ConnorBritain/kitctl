import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(repo)
    return repo


def link(source: Path, target: Path) -> None:
    """Create a symlink or skip with a clear reason where the runner can't."""
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:
        reason = "Windows needs Developer Mode or admin" if os.name == "nt" else str(error)
        pytest.skip(f"Symlink creation unavailable: {reason}")
