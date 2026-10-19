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
    monkeypatch.chdir(repo)
    return repo


def make_git_repo(source: Path, destination: Path) -> Path:
    """Commit a fixture tree into a fresh local repository (no network)."""
    import shutil
    import subprocess

    shutil.copytree(source, destination)
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-01T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-10-01T12:00:00+00:00",
    }
    for args in (
        ["init", "--quiet", "--initial-branch=main"],
        ["add", "--all"],
        ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=destination, check=True, env=env)
    return destination


@pytest.fixture
def scout_repos(tmp_path: Path) -> dict[str, Path]:
    base = Path(__file__).parent / "fixtures" / "scout"
    return {
        name: make_git_repo(base / name, tmp_path / "upstream" / "example" / name)
        for name in ("good-skill", "telemetry-mcp", "unlicensed-skill")
    }
