from pathlib import Path

import pytest

from kitctl import paths
from kitctl.inventory import allowed_roots


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
    assert resolved.claude_managed == (Path("/Library/Application Support/ClaudeCode"),)


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


def test_inventory_allowed_roots_follow_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-claude"
    custom.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    assert custom.resolve() in allowed_roots(())
