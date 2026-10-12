from pathlib import Path

import pytest

from kitctl import paths
from kitctl.inventory import inventory
from tests.conftest import ORIGINAL_MANAGED, link


def test_honors_claude_and_codex_overrides(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "alt-claude"),
        "CODEX_HOME": str(tmp_path / "alt-codex"),
    }
    resolved = paths.resolve(env, "linux")
    assert resolved.claude_config_dir == tmp_path / "alt-claude"
    assert resolved.claude_json == tmp_path / "alt-claude/.claude.json"
    assert resolved.codex_home == tmp_path / "alt-codex"


def test_defaults_from_home(tmp_path: Path) -> None:
    resolved = paths.resolve({"HOME": str(tmp_path)}, "darwin")
    assert resolved.claude_config_dir == tmp_path / ".claude"
    assert resolved.claude_json == tmp_path / ".claude.json"
    assert resolved.codex_home == tmp_path / ".codex"
    assert resolved.kit_config_dir == tmp_path / ".config/kitctl"
    assert resolved.kit_cache_dir == tmp_path / "Library/Caches/kitctl"
    assert resolved.default_warehouse == tmp_path / "kits"
    assert ORIGINAL_MANAGED("macos", {})[0] == (Path("/Library/Application Support/ClaudeCode"),)
    assert ORIGINAL_MANAGED("linux", {}) == ((Path("/etc/claude-code"),), (Path("/etc/codex"),))
    assert ORIGINAL_MANAGED("windows", {})[0][0] == Path("C:/Program Files/ClaudeCode")


def test_linux_xdg(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": "/x/cfg", "XDG_CACHE_HOME": "/x/cache"}
    resolved = paths.resolve(env, "linux")
    assert resolved.kit_config_dir == Path("/x/cfg/kitctl")
    assert resolved.kit_cache_dir == Path("/x/cache/kitctl")
    assert paths.resolve({"HOME": str(tmp_path)}, "linux").kit_cache_dir == (
        tmp_path / ".cache/kitctl"
    )


def test_windows_userprofile_and_appdata(tmp_path: Path) -> None:
    env = {
        "USERPROFILE": str(tmp_path / "user"),
        "HOME": str(tmp_path / "ignored"),
        "APPDATA": str(tmp_path / "roaming"),
        "LOCALAPPDATA": str(tmp_path / "local"),
    }
    resolved = paths.resolve(env, "win32")
    assert resolved.home == tmp_path / "user"
    assert resolved.kit_config_dir == tmp_path / "roaming/kitctl"
    assert resolved.kit_cache_dir == tmp_path / "local/kitctl/Cache"


def test_inventory_reads_relocated_claude_dir(
    tmp_path: Path, sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-claude"
    (custom / "skills/shared").mkdir(parents=True)
    (custom / "skills/shared/SKILL.md").write_text("shared")
    (custom / "CLAUDE.md").write_text("user")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    (sandbox / ".claude/skills").mkdir(parents=True)
    link(sandbox / ".claude/skills/shared", custom / "skills/shared")
    report = inventory([sandbox])
    user = [e for e in report["entries"] if e["scope"] == "user"]
    assert {e["name"] for e in user} == {"shared", "CLAUDE.md"}
    project = [e for e in report["entries"] if e["kind"] == "skill" and e["scope"] == "project"]
    shared = [e for e in user if e["name"] == "shared"][0]
    assert project[0]["content_hash"] == shared["content_hash"]
    assert project[0]["link_target"] == str((custom / "skills/shared").resolve())
