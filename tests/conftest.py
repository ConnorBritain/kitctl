import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from kitctl import paths as kit_paths

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANARY = "CANARY-VALUE-7f3a"
ORIGINAL_MANAGED = kit_paths._managed
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/report.schema.json").read_text())


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
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "CLAUDE_CODE_PLUGIN_CACHE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    managed = (tmp_path / "managed-claude",), (tmp_path / "managed-codex",)
    monkeypatch.setattr(kit_paths, "_managed", lambda platform, env: managed)
    monkeypatch.chdir(repo)
    return repo


def link(source: Path, target: Path) -> None:
    """Create a symlink or skip with a clear reason where the runner can't."""
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:
        reason = "Windows needs Developer Mode or admin" if os.name == "nt" else str(error)
        pytest.skip(f"Symlink creation unavailable: {reason}")


def install_fixture(name: str, tmp_path: Path) -> None:
    """Copy fixtures/<name>/{home,repo,managed} into the sandbox, expanding tokens."""
    source = FIXTURES / name
    home, repo = tmp_path / "home", tmp_path / "repo"
    managed = tmp_path / ("managed-claude" if name.startswith("claude") else "managed-codex")
    tokens = {
        "{{HOME}}": home.as_posix(),
        "{{REPO}}": str(repo.resolve()).replace("\\", "/"),
        "{{PLUGINS}}": (home / ".claude/plugins").as_posix(),
        "{{CANARY}}": CANARY,
    }
    for part, target in (("home", home), ("repo", repo), ("managed", managed)):
        root = source / part
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            destination = target / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            for token, value in tokens.items():
                data = data.replace(token.encode(), value.encode())
            destination.write_bytes(data)
    (repo / ".git").mkdir(exist_ok=True)


@pytest.fixture
def harnesses(sandbox: Path, tmp_path: Path) -> Path:
    install_fixture("claude/2.1.296", tmp_path)
    install_fixture("codex/synthetic", tmp_path)
    return sandbox


def validate(report: dict[str, Any]) -> None:
    import jsonschema

    jsonschema.validate(report, SCHEMA)


def find(report: dict[str, Any], **criteria: Any) -> list[dict[str, Any]]:
    return [
        entry
        for entry in report["entries"]
        if all(entry.get(key) == value for key, value in criteria.items())
    ]


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True)
