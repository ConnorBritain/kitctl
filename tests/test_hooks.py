"""Capture hooks (reconcile, guard, capture install/remove) and marketplace build."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from kitctl import capture, inventory
from kitctl import paths as kit_paths
from kitctl.cli import main
from tests.kits import make_warehouse, primitive, snapshot, write

KITCTL = "/opt/kitctl/bin/kitctl"


@pytest.fixture
def env(sandbox: Path, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    warehouse = make_warehouse(home / "kits")
    write(home / ".claude/settings.json", '{\n  "model": "opus"\n}\n')
    write(home / ".claude/skills/existing/SKILL.md", "---\nname: existing\n---\nOld.\n")
    write(home / ".codex/config.toml", 'model = "o3"\n')
    (sandbox / ".git").mkdir(exist_ok=True)
    return {"home": home, "repo": sandbox, "warehouse": warehouse}


def config_files(env: dict[str, Path]) -> dict[str, bytes]:
    home = env["home"]
    return snapshot(home / ".claude", home / ".codex", home / ".agents")


def test_capture_install_and_remove(env: dict[str, Path]) -> None:
    before = config_files(env)
    assert main(["capture", "install", "--command", KITCTL, "--dry-run"]) == 0
    assert config_files(env) == before
    assert main(["capture", "install", "--command", KITCTL, "--yes"]) == 0
    settings = json.loads((env["home"] / ".claude/settings.json").read_text())
    assert settings["model"] == "opus"
    start = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert start == f"{KITCTL} reconcile --hook claude"
    guard = settings["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == capture.CLAUDE_GUARD_MATCHER
    codex = json.loads((env["home"] / ".codex/hooks.json").read_text())
    assert codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"].endswith("--harness codex")
    assert main(["capture", "install", "--command", KITCTL, "--yes"]) == 0  # idempotent
    assert main(["capture", "remove", "--yes"]) == 0
    assert config_files(env) == before


def test_capture_refuses_npx_launcher(env: dict[str, Path], monkeypatch, tmp_path) -> None:
    launcher = tmp_path / "node_modules/.bin/kitctl"
    write(launcher, "#!/bin/sh\n", mode=0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(launcher))
    with pytest.raises(ValueError, match="npm launcher"):
        capture.executable()


def test_reconcile_captures_only_new_items(env: dict[str, Path]) -> None:
    paths = kit_paths.resolve()
    repo = env["repo"]
    capture.save_state(paths, capture.baseline(paths, repo))
    assert capture.reconcile(paths, repo, env["warehouse"]) == []
    write(env["home"] / ".claude/skills/fresh/SKILL.md", "---\nname: fresh\n---\nNew.\n")
    claude_json = env["home"] / ".claude/.claude.json"
    write(
        claude_json,
        json.dumps(
            {"mcpServers": {"api": {"command": "api-mcp", "env": {"KEY": "literal-value"}}}}
        ),
    )
    captured = capture.reconcile(paths, repo, env["warehouse"])
    assert sorted(captured) == ["mcp_server api", "skill fresh"]
    inbox = env["warehouse"] / "inbox"
    assert (inbox / "skills/fresh/files/SKILL.md").is_file()
    assert not (inbox / "skills/existing").exists()
    server = json.loads((inbox / "mcp/api/server.json").read_text())
    assert server["api"]["env"] == {"KEY": "${KEY}"}
    assert "literal-value" not in json.dumps(server)
    assert capture.reconcile(paths, repo, env["warehouse"]) == []


def test_reconcile_fast_path_skips_inventory(env: dict[str, Path], monkeypatch) -> None:
    paths = kit_paths.resolve()
    capture.save_state(paths, capture.baseline(paths, env["repo"]))

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("inventory ran on the no-change path")

    monkeypatch.setattr(inventory, "inventory", boom)
    started = time.perf_counter()
    assert capture.reconcile(paths, env["repo"], env["warehouse"]) == []
    assert time.perf_counter() - started < 0.3


def test_reconcile_ignores_kitctl_owned_items(env: dict[str, Path]) -> None:
    paths = kit_paths.resolve()
    capture.save_state(paths, capture.baseline(paths, env["repo"]))
    assert main(["apply", "--global", "--yes"]) == 0
    assert capture.reconcile(paths, env["repo"], env["warehouse"]) == []


def test_reconcile_hook_fails_open(env: dict[str, Path], monkeypatch, capsys) -> None:
    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(capture, "reconcile", broken)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/nowhere"}'))
    assert main(["reconcile", "--hook", "claude"]) == 0
    assert "skipped" in capsys.readouterr().err
    monkeypatch.setenv(capture.APPLYING, "1")
    assert main(["reconcile", "--hook", "claude"]) == 0


def _guard(monkeypatch, capsys, payload: dict[str, object], harness: str = "claude") -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["guard", "--harness", harness]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"] if out else {}


def test_guard_decisions(env: dict[str, Path], monkeypatch, capsys) -> None:
    home, repo = env["home"], env["repo"]
    skill = home / ".claude/skills/writer/SKILL.md"
    assert (
        _guard(monkeypatch, capsys, {"tool_name": "Edit", "tool_input": {"file_path": str(skill)}})
        == {}
    )  # nothing owned yet
    assert main(["apply", "--global", "--yes"]) == 0
    capsys.readouterr()
    denied = _guard(
        monkeypatch,
        capsys,
        {"tool_name": "Edit", "tool_input": {"file_path": str(skill)}, "cwd": str(repo)},
    )
    assert (
        denied["permissionDecision"] == "deny"
        and "kitctl apply" in denied["permissionDecisionReason"]
    )
    shared = home / ".claude/settings.json"
    asked = _guard(
        monkeypatch, capsys, {"tool_name": "Write", "tool_input": {"file_path": str(shared)}}
    )
    assert asked["permissionDecision"] == "ask"
    assert (
        _guard(
            monkeypatch,
            capsys,
            {"tool_name": "Write", "tool_input": {"file_path": str(shared)}},
            harness="codex",
        )
        == {}
    )
    rm = _guard(
        monkeypatch,
        capsys,
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/.claude/skills/writer/SKILL.md"}},
    )
    assert rm["permissionDecision"] == "deny"
    assert (
        _guard(
            monkeypatch, capsys, {"tool_name": "Bash", "tool_input": {"command": f"cat {skill}"}}
        )
        == {}
    )
    patch = f"*** Begin Patch\n*** Update File: {skill}\n@@\n-a\n+b\n*** End Patch\n"
    patched = _guard(
        monkeypatch,
        capsys,
        {"tool_name": "apply_patch", "tool_input": {"input": patch}},
        harness="codex",
    )
    assert patched["permissionDecision"] == "deny"
    monkeypatch.setenv(capture.APPLYING, "1")
    assert (
        _guard(monkeypatch, capsys, {"tool_name": "Edit", "tool_input": {"file_path": str(skill)}})
        == {}
    )


def test_guard_fails_open_on_garbage(env: dict[str, Path], monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["guard"]) == 0
    assert capsys.readouterr().out == ""


def test_marketplace_build(env: dict[str, Path], tmp_path: Path) -> None:
    warehouse = env["warehouse"]
    plugin = primitive(warehouse, "plugins", "plugin", "tools", harnesses='["claude"]')
    write(
        plugin / "files/.claude-plugin/plugin.json",
        json.dumps({"name": "tools", "version": "1.2.0", "description": "Tools."}),
    )
    write(plugin / "files/skills/hello/SKILL.md", "---\nname: hello\ndescription: Hi.\n---\n")
    assert main(["marketplace", "build", "--dry-run"]) == 0
    assert not (warehouse / "marketplace").exists()
    assert main(["marketplace", "build"]) == 0
    root = warehouse / "marketplace"
    listing = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    names = [p["name"] for p in listing["plugins"]]
    assert names == ["tools", "loadout-core", "loadout-web"]
    core = root / "plugins/loadout-core"
    assert (core / "skills/writer/SKILL.md").is_file()
    hooks = json.loads((core / "hooks/hooks.json").read_text())
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/lint/lint.sh" in json.dumps(hooks)
    assert json.loads((core / ".mcp.json").read_text())["mcpServers"]["github"]["command"]
    manifest = json.loads((core / ".claude-plugin/plugin.json").read_text())
    assert manifest["version"].startswith("0.0.0-kitctl.")
    before = snapshot(root)
    assert main(["marketplace", "build"]) == 0
    assert snapshot(root) == before

    claude = shutil.which("claude")
    if claude:
        result = subprocess.run(
            [claude, "plugin", "validate", str(root)],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HOME": str(tmp_path / "validate-home")},
        )
        assert result.returncode == 0, result.stdout + result.stderr

    write(
        warehouse / "loadouts/core.toml", 'schema_version = 1\nname = "core"\nplugins = ["tools"]\n'
    )
    assert main(["apply", "--global", "--yes"]) == 0
    settings = json.loads((env["home"] / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"] == {"tools@kitctl": True}
    source = settings["extraKnownMarketplaces"]["kitctl"]["source"]
    assert source == {"source": "directory", "path": str(root)}
    assert main(["eject", "--yes"]) == 0
    settings = json.loads((env["home"] / ".claude/settings.json").read_text())
    moved = Path(settings["extraKnownMarketplaces"]["kitctl"]["source"]["path"])
    assert moved == env["home"] / ".claude/kitctl-marketplace"
    assert (moved / ".claude-plugin/marketplace.json").is_file()


def test_plugin_not_in_marketplace_is_a_gap(env: dict[str, Path], capsys) -> None:
    primitive(env["warehouse"], "plugins", "plugin", "tools", harnesses='["claude"]')
    write(
        env["warehouse"] / "loadouts/core.toml",
        'schema_version = 1\nname = "core"\nplugins = ["tools"]\n',
    )
    assert main(["apply", "--global", "--yes"]) == 1
    assert "marketplace build" in capsys.readouterr().err
