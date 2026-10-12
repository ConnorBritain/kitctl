"""Regressions from the adversarial review of the write engine (one test per finding)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from kitctl import capture, engine
from kitctl import paths as kit_paths
from kitctl.cli import main
from kitctl.compile import compile_desired
from kitctl.desired import Warehouse, desired_user
from kitctl.validate import validate
from tests.conftest import link
from tests.kits import make_warehouse, primitive, snapshot, write


@pytest.fixture
def env(sandbox: Path, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    warehouse = make_warehouse(home / "kits")
    (home / ".claude").mkdir(parents=True)
    (sandbox / ".git").mkdir(exist_ok=True)
    return {"home": home, "repo": sandbox, "warehouse": warehouse}


def core(env: dict[str, Path], text: str) -> None:
    write(env["warehouse"] / "loadouts/core.toml", 'schema_version = 1\nname = "core"\n' + text)


def files(env: dict[str, Path]) -> dict[str, bytes]:
    home = env["home"]
    return snapshot(home / ".claude", home / ".codex", home / ".agents", env["repo"])


def test_inline_mcp_table_is_a_conflict(env: dict[str, Path], capsys) -> None:
    config = env["home"] / ".codex/config.toml"
    write(config, 'mcp_servers = { mine = { command = "mine" } }\n')
    core(env, 'mcp = ["github"]\n')
    assert main(["apply", "--global", "--yes"]) == 1
    assert "inline table" in capsys.readouterr().out
    assert config.read_text() == 'mcp_servers = { mine = { command = "mine" } }\n'


def test_literal_credentials_in_server_json_are_refused(env: dict[str, Path], capsys) -> None:
    server = env["warehouse"] / "primitives/mcp/github/server.json"
    token = "ghp_" + "A" * 36
    write(server, json.dumps({"github": {"command": "gh", "env": {"GITHUB_TOKEN": token}}}))
    assert any(f["code"] == "literal-credential" for f in validate(env["warehouse"])["findings"])
    before = files(env)
    assert main(["apply", "--global", "--yes"]) == 2
    assert files(env) == before
    write(server, json.dumps({"github": {"command": "gh", "args": ["--api-key", "abc123"]}}))
    assert main(["validate"]) == 1


def test_adopt_and_reconcile_refuse_secrets(env: dict[str, Path]) -> None:
    paths = kit_paths.resolve()
    capture.save_state(paths, capture.baseline(paths, env["repo"]))
    skill = env["home"] / ".claude/skills/mine"
    write(skill / "SKILL.md", "---\nname: mine\n---\ntoken ghp_" + "B" * 36 + "\n")
    other = env["home"] / ".claude/skills/envy"
    write(other / "SKILL.md", "---\nname: envy\n---\n")
    write(other / ".env", "X=1\n")
    write(env["repo"] / ".claude/skills/theirs/SKILL.md", "---\nname: theirs\n---\n")
    assert capture.reconcile(paths, env["repo"], env["warehouse"]) == []
    assert not (env["warehouse"] / "inbox").exists()


def test_adopted_items_are_restored_after_updates(env: dict[str, Path]) -> None:
    home = env["home"]
    agent = home / ".claude/agents/reviewer.md"
    write(agent, "---\nname: reviewer\n---\nReview code.\n")
    claude_json = home / ".claude/.claude.json"
    server = {"type": "stdio", "command": "gh-mcp", "args": ["--stdio"],
              "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}  # fmt: skip
    write(claude_json, json.dumps({"mcpServers": {"github": server}}, indent=2) + "\n")
    core(env, 'mcp = ["github"]\nagents = ["reviewer"]\n')
    meta = env["warehouse"] / "primitives/mcp/github/primitive.toml"
    meta.write_text(meta.read_text().replace('["claude", "codex"]', '["claude"]'))
    baseline = files(env)
    assert main(["apply", "--global", "--yes"]) == 0
    assert files(env) == baseline  # identical: adopted, nothing written
    write(env["warehouse"] / "primitives/agents/reviewer/files/reviewer.md", "v2\n")
    server_json = env["warehouse"] / "primitives/mcp/github/server.json"
    write(server_json, json.dumps({"github": {"command": "gh-mcp", "args": ["--v2"]}}))
    assert main(["apply", "--global", "--yes"]) == 0
    assert agent.read_text() == "v2\n"
    assert main(["purge", "--global", "--yes"]) == 0
    assert files(env) == baseline


@pytest.mark.parametrize(
    ("name", "original"),
    [
        ("settings.json", '{"model":"opus","x":"café"}'),
        ("settings.json", '{\n    "b": 1,\n    "a": 2\n}'),
    ],
)
def test_purge_restores_json_bytes(env: dict[str, Path], name: str, original: str) -> None:
    write(env["home"] / ".claude" / name, original)
    core(env, 'plugins = ["formatter@team-tools"]\n')
    baseline = files(env)
    assert main(["apply", "--global", "--yes"]) == 0
    assert main(["purge", "--global", "--yes"]) == 0
    assert files(env) == baseline


def test_purge_restores_toml_bytes(env: dict[str, Path]) -> None:
    config = env["home"] / ".codex/config.toml"
    for original in (
        'model = "o3"',
        '[mcp_servers]\n\n[profiles.x]\nmodel = "o3"\n',
        '# mine\n[mcp_servers.github]\ncommand = "old" # keep\n[mcp_servers.github.tools.search]\n'
        "approval = true\n",
    ):
        write(config, original)
        core(env, 'mcp = ["github"]\n')
        baseline = files(env)
        assert main(["apply", "--global", "--yes", "--replace"]) == 0
        assert main(["purge", "--global", "--yes"]) == 0
        assert files(env) == baseline, original


def test_edited_hook_is_drift_not_a_duplicate(env: dict[str, Path], capsys) -> None:
    core(env, 'hooks = ["lint"]\n')
    assert main(["apply", "--global", "--yes"]) == 0
    settings = env["home"] / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 5
    settings.write_text(json.dumps(data, indent=2) + "\n")
    capsys.readouterr()
    assert main(["apply", "--global", "--yes"]) == 1
    assert "edited since kitctl wrote it" in capsys.readouterr().out
    assert len(json.loads(settings.read_text())["hooks"]["PreToolUse"]) == 1
    assert main(["purge", "--global", "--yes"]) == 1
    assert (env["home"] / ".claude/hooks/kitctl/lint/lint.sh").is_file()


def test_two_owners_leave_no_empty_containers(env: dict[str, Path]) -> None:
    core(env, 'hooks = ["lint"]\n')
    baseline = files(env)
    assert main(["apply", "--global", "--yes"]) == 0
    assert main(["capture", "install", "--command", "/opt/k/kitctl", "--yes"]) == 0
    assert main(["purge", "--global", "--yes"]) == 0
    assert main(["purge", "--capture", "--yes"]) == 0
    assert files(env) == baseline


def test_directory_in_place_of_a_file_is_a_conflict(env: dict[str, Path], capsys) -> None:
    (env["home"] / ".claude/agents/reviewer.md").mkdir(parents=True)
    core(env, 'agents = ["reviewer"]\nskills = ["writer"]\n')
    before = files(env)
    assert main(["apply", "--global", "--yes"]) == 1
    assert "isn't a regular file" in capsys.readouterr().out
    assert files(env) == before
    assert engine.pending(kit_paths.resolve()) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_rollback_keeps_file_modes(env: dict[str, Path]) -> None:
    claude_json = env["home"] / ".claude/.claude.json"
    write(claude_json, "{}\n")
    claude_json.chmod(0o600)
    core(env, 'mcp = ["github"]\n')
    paths = kit_paths.resolve()
    warehouse = Warehouse(env["warehouse"])
    compiled = compile_desired(desired_user(warehouse), paths, warehouse.root)
    plan = engine.plan("user", compiled.effects, paths)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(plan, paths, crash_after=1)  # .claude.json swapped, config.toml not
    assert main(["recover", "--rollback"]) == 0
    assert claude_json.stat().st_mode & 0o777 == 0o600
    assert main(["apply", "--global", "--yes"]) == 0
    assert (paths.kit_state_dir / "ledger.json").stat().st_mode & 0o777 == 0o600


def test_symlinked_warehouse_files_are_refused(env: dict[str, Path], tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside / "id_rsa", "private\n")
    evil = primitive(env["warehouse"], "skills", "skill", "evil")
    link(evil / "files", outside)
    assert any(f["code"] == "symlink" for f in validate(env["warehouse"])["findings"])
    core(env, 'skills = ["evil"]\n')
    assert main(["apply", "--global", "--yes"]) == 2
    assert not (env["home"] / ".claude/skills/evil").exists()


def test_codex_project_script_hook_is_a_gap(env: dict[str, Path], capsys) -> None:
    core(env, "")
    write(env["warehouse"] / "loadouts/web.toml", 'schema_version = 1\nname = "web"\n'
          'hooks = ["lint"]\n')  # fmt: skip
    write(env["repo"] / "kit.toml", 'schema_version = 1\nloadouts = ["web"]\n')
    assert main(["apply", "--repo", str(env["repo"]), "--yes"]) == 1
    assert "absolute" in capsys.readouterr().err
    write(env["repo"] / "kit.toml", 'schema_version = 1\nloadouts = ["web"]\nharnesses = '
          '["claude"]\n')  # fmt: skip
    assert main(["apply", "--repo", str(env["repo"]), "--yes"]) == 0
    settings = (env["repo"] / ".claude/settings.json").read_text()
    assert "$CLAUDE_PROJECT_DIR" in settings


def test_eject_with_capture_and_marketplace_in_one_run(env: dict[str, Path]) -> None:
    plugin = primitive(env["warehouse"], "plugins", "plugin", "tools", harnesses='["claude"]')
    write(plugin / "files/.claude-plugin/plugin.json", json.dumps({"name": "tools"}))
    core(env, 'plugins = ["tools"]\n')
    assert main(["marketplace", "build"]) == 0
    assert main(["apply", "--global", "--yes"]) == 0
    assert main(["capture", "install", "--command", "/opt/k/kitctl", "--yes"]) == 0
    assert main(["eject", "--yes"]) == 0
    assert engine.Ledger(kit_paths.resolve()).records == {}


def _guard(monkeypatch, capsys, command: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}})))  # fmt: skip
    assert main(["guard"]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def test_guard_redirects_and_directories(env: dict[str, Path], monkeypatch, capsys) -> None:
    core(env, 'skills = ["writer"]\n')
    assert main(["apply", "--global", "--yes"]) == 0
    capsys.readouterr()
    assert _guard(monkeypatch, capsys, "cat ~/.claude/skills/writer/SKILL.md 2>/dev/null") == (
        "allow"
    )
    assert _guard(monkeypatch, capsys, "rm -rf ~/.claude/skills/writer") == "deny"
    assert _guard(monkeypatch, capsys, "find ~/.claude/skills/writer -name x -delete") == "deny"


def test_hooks_never_exit_nonzero_on_bad_arguments(env: dict[str, Path]) -> None:
    assert main(["guard", "--harness", "nonsense"]) == 0
    assert main(["reconcile", "--unknown-flag"]) == 0
    with pytest.raises(SystemExit):
        main(["apply", "--nonsense"])
