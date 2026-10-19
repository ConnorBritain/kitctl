"""kitctl sync (git pull + apply everything managed) and machine profiles."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.desired import Warehouse, desired_user
from tests.kits import make_warehouse, write

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def setup(sandbox: Path, tmp_path: Path, monkeypatch) -> dict[str, Path]:
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                       "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    source = make_warehouse(tmp_path / "source")
    git("init", "-q", "-b", "main", cwd=source)
    git("add", "-A", cwd=source)
    git("commit", "-qm", "kits", cwd=source)
    bare = tmp_path / "kits.git"
    git("clone", "-q", "--bare", str(source), str(bare), cwd=tmp_path)
    home = tmp_path / "home"
    git("clone", "-q", str(bare), str(home / "kits"), cwd=tmp_path)
    write(sandbox / "kit.toml", 'schema_version = 1\nloadouts = ["web"]\n')
    assert main(["init", "--non-interactive", "--warehouse", str(home / "kits"),
                 "--repo-parent", str(tmp_path)]) == 0  # fmt: skip
    return {"source": source, "bare": bare, "home": home, "repo": sandbox}


def test_sync_pulls_and_applies(setup: dict[str, Path], capsys) -> None:
    home, repo = setup["home"], setup["repo"]
    assert main(["apply", "--global", "--yes"]) == 0
    assert main(["apply", "--repo", str(repo), "--yes"]) == 0
    assert main(["sync", "--dry-run"]) == 0
    assert "Dry run: 0 changes" in capsys.readouterr().out

    # Another machine changes the warehouse and pushes.
    source = setup["source"]
    write(source / "primitives/skills/writer/files/SKILL.md",
          "---\nname: writer\ndescription: Writes better.\n---\n")  # fmt: skip
    write(source / "primitives/rules/style/files/style.md", "# Style\nShorter.\n")
    git("commit", "-qam", "update", cwd=source)
    git("push", "-q", str(setup["bare"]), "main", cwd=source)

    capsys.readouterr()
    assert main(["sync", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "git -C" in out and "Synced 2 scopes" in out
    assert "better" in (home / ".claude/skills/writer/SKILL.md").read_text()
    assert "Shorter." in (repo / "AGENTS.md").read_text()
    assert main(["sync", "--dry-run", "--no-pull"]) == 0


def test_sync_stops_when_pull_fails(setup: dict[str, Path], capsys) -> None:
    shutil.rmtree(setup["bare"])
    assert main(["sync", "--yes"]) == 2
    assert "git pull failed" in capsys.readouterr().err
    assert not (setup["home"] / ".claude/skills").exists()


def test_machine_profile(setup: dict[str, Path]) -> None:
    kits = setup["home"] / "kits"
    write(kits / "loadouts/machine-windows.toml",
          'schema_version = 1\nname = "machine-windows"\nrules = ["style"]\n')  # fmt: skip
    warehouse = Warehouse(kits)
    assert "rules:style" in {i.ref for i in desired_user(warehouse, "windows").items}
    assert "rules:style" not in {i.ref for i in desired_user(warehouse, "").items}
    note = desired_user(warehouse, "mac").notes
    assert any("machine-mac" in n for n in note)
    assert main(["apply", "--global", "--profile", "windows", "--yes"]) == 0
    assert (setup["home"] / ".claude/rules/style.md").is_file()
    settings = json.loads((setup["home"] / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"]
