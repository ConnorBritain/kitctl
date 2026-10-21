"""`kitctl init`: a starter warehouse that validates and applies on its own."""

import json
from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main


def test_init_creates_a_warehouse_that_validates_and_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    kits = tmp_path / "kits"
    assert main(["init", str(kits), "--no-git"]) == 0
    assert "created a warehouse" in capsys.readouterr().out
    assert (kits / "loadouts" / "starter.yaml").is_file()
    assert all((kits / "primitives" / kind).is_dir() for kind in ("skills", "mcp", "hooks"))
    assert not (kits / ".git").exists()
    assert main(["validate", str(kits), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []

    repo = tmp_path / "project"
    repo.mkdir()
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path))
    args = ["--repo", str(repo), "--warehouse", str(kits), "--yes"]
    assert main(["use", "starter", *args]) == 0
    assert "managed by kitctl" in (repo / "CLAUDE.md").read_text()
    assert main(["off", "starter", *args]) == 0
    assert not (repo / "CLAUDE.md").exists()


def test_init_defaults_to_kit_warehouse_and_refuses_a_non_empty_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    kits = tmp_path / "mine"
    monkeypatch.setenv("KIT_WAREHOUSE", str(kits))
    assert main(["init", "--no-git"]) == 0
    assert (kits / "README.md").is_file()
    before = (kits / "README.md").read_bytes()
    assert main(["init"]) == 1
    assert "not empty" in capsys.readouterr().err
    assert (kits / "README.md").read_bytes() == before


def test_init_accepts_an_empty_directory_and_runs_git_init(tmp_path: Path) -> None:
    kits = tmp_path / "empty"
    kits.mkdir()
    assert main(["init", str(kits)]) == 0
    assert (kits / ".git").exists() or not __import__("shutil").which("git")
