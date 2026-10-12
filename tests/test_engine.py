"""Write-engine acceptance suite (docs/design.md "Acceptance before live apply").

Everything runs against a throwaway HOME, CODEX_HOME, CLAUDE_CONFIG_DIR, and repository.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kitctl import engine
from kitctl import paths as kit_paths
from kitctl.cli import main
from kitctl.compile import codex_server, compile_desired
from kitctl.desired import DesiredError, Warehouse, desired_repo, desired_user
from tests.audit import guarded
from tests.kits import make_warehouse, snapshot, write


@pytest.fixture
def env(sandbox: Path, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    warehouse = make_warehouse(home / "kits")
    claude = home / ".claude"
    write(
        claude / ".claude.json",
        json.dumps({"numStartups": 3, "mcpServers": {"other": {"command": "x"}}}, indent=2) + "\n",
    )
    write(claude / "settings.json", '{\n  "model": "opus"\n}\n')
    write(
        home / ".codex/config.toml",
        '# mine\nmodel = "o3"\n\n[mcp_servers.mine]\ncommand = "mine"  # keep this comment\n',
    )
    write(home / ".codex/AGENTS.md", "# House rules\nBe kind.\n")
    write(sandbox / "kit.toml", 'schema_version = 1\nloadouts = ["web"]\n')
    write(sandbox / "AGENTS.md", "# Repo\n")
    return {"home": home, "repo": sandbox, "warehouse": warehouse}


def agent_files(env: dict[str, Path]) -> dict[str, bytes]:
    home = env["home"]
    return snapshot(home / ".claude", home / ".codex", home / ".agents", env["repo"])


def run(*argv: str) -> int:
    return main(list(argv))


def test_dry_run_writes_nothing(env: dict[str, Path]) -> None:
    before = agent_files(env)
    paths = kit_paths.resolve()
    with guarded([paths.kit_config_dir, paths.kit_cache_dir]) as violations:
        assert run("apply", "--global", "--dry-run") == 0
        assert run("apply", "--repo", str(env["repo"]), "--dry-run") == 0
        assert run("desired", "--repo", str(env["repo"])) == 0
    assert violations == []
    assert agent_files(env) == before


def test_apply_reapply_purge_round_trip(env: dict[str, Path], capsys) -> None:
    baseline = agent_files(env)
    assert run("apply", "--global", "--yes") == 0
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 0
    home, repo = env["home"], env["repo"]
    claude_json = json.loads((home / ".claude/.claude.json").read_text())
    assert claude_json["numStartups"] == 3 and set(claude_json["mcpServers"]) == {"other", "github"}
    assert claude_json["mcpServers"]["github"]["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    toml = (home / ".codex/config.toml").read_text()
    assert "# keep this comment" in toml and 'env_vars = ["GITHUB_TOKEN"]' in toml
    assert (home / ".claude/skills/writer/templates/note.md").is_file()
    assert (home / ".agents/skills/writer/SKILL.md").is_file()
    script = home / ".claude/hooks/kitctl/lint/lint.sh"
    if os.name != "nt":
        assert script.stat().st_mode & 0o111
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings["model"] == "opus" and settings["enabledPlugins"] == {
        "formatter@team-tools": True
    }
    assert json.loads((repo / ".mcp.json").read_text())["mcpServers"]["docs"]["url"]
    assert "bearer_token_env_var" in (repo / ".codex/config.toml").read_text()
    assert "<!-- kitctl:begin rules:style -->" in (repo / "AGENTS.md").read_text()
    lock = json.loads((repo / "kit.lock").read_text())
    assert lock["loadouts"] == ["web"] and "rules:style" in lock["items"]
    assert "preimage" not in (repo / "kit.lock").read_text()
    capsys.readouterr()

    applied = agent_files(env)
    assert run("apply", "--global", "--dry-run") == 0
    assert run("apply", "--repo", str(repo), "--dry-run") == 0
    assert capsys.readouterr().out.count("Nothing to change.") == 2
    assert agent_files(env) == applied
    assert run("status") == 0

    assert run("purge", "--all", "--yes") == 0
    assert agent_files(env) == baseline
    assert engine.Ledger(kit_paths.resolve()).records == {}


def test_warehouse_change_updates_owned_files(env: dict[str, Path]) -> None:
    assert run("apply", "--global", "--yes") == 0
    skill = env["warehouse"] / "primitives/skills/writer/files"
    (skill / "templates/note.md").unlink()
    write(skill / "SKILL.md", "---\nname: writer\ndescription: Writes better.\n---\n")
    assert run("apply", "--global", "--yes") == 0
    installed = env["home"] / ".claude/skills/writer"
    assert "better" in (installed / "SKILL.md").read_text()
    assert not (installed / "templates").exists()


def test_unrelated_user_edits_survive_apply_and_purge(env: dict[str, Path]) -> None:
    assert run("apply", "--global", "--yes") == 0
    home = env["home"]
    claude_json = home / ".claude/.claude.json"
    data = json.loads(claude_json.read_text())
    data["mcpServers"]["added-later"] = {"command": "y"}
    data["theme"] = "dark"
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    toml = home / ".codex/config.toml"
    toml.write_text(toml.read_text() + '\n# later\n[mcp_servers.later]\ncommand = "z"\n')
    assert run("purge", "--global", "--yes") == 0
    data = json.loads(claude_json.read_text())
    assert set(data["mcpServers"]) == {"other", "added-later"} and data["theme"] == "dark"
    text = toml.read_text()
    assert "github" not in text and "[mcp_servers.later]" in text and "# keep this comment" in text


def test_drift_is_a_conflict(env: dict[str, Path], capsys) -> None:
    assert run("apply", "--global", "--yes") == 0
    claude_json = env["home"] / ".claude/.claude.json"
    data = json.loads(claude_json.read_text())
    data["mcpServers"]["github"]["args"] = ["--edited"]
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    assert run("status") == 1
    assert "drifted" in capsys.readouterr().out
    before = agent_files(env)
    assert run("purge", "--global", "--yes") == 1
    assert "CONFLICT mcp:github" in capsys.readouterr().out
    assert agent_files(env) == before
    write(env["warehouse"] / "primitives/skills/writer/files/extra.md", "new\n")
    assert run("apply", "--global", "--yes") == 1  # conflicts block the whole apply
    assert agent_files(env) == before


def test_existing_entries(env: dict[str, Path], capsys) -> None:
    claude_json = env["home"] / ".claude/.claude.json"
    data = json.loads(claude_json.read_text())
    data["mcpServers"]["github"] = {"command": "old-gh"}
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    original = claude_json.read_bytes()
    assert run("apply", "--global", "--yes") == 1
    assert "isn't kitctl's" in capsys.readouterr().out
    assert run("apply", "--global", "--yes", "--replace") == 0
    assert json.loads(claude_json.read_text())["mcpServers"]["github"]["command"] == "gh-mcp"
    assert run("purge", "--global", "--yes") == 0
    assert claude_json.read_bytes() == original

    data["mcpServers"]["github"] = {"command": "gh", "env": {"GITHUB_TOKEN": "ghp_" + "a" * 36}}
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    assert run("apply", "--global", "--yes", "--replace") == 1
    assert "literal credentials" in capsys.readouterr().out


def test_identical_existing_entry_is_adopted_and_kept(env: dict[str, Path]) -> None:
    claude_json = env["home"] / ".claude/.claude.json"
    data = json.loads(claude_json.read_text())
    data["mcpServers"]["github"] = {
        "type": "stdio",
        "command": "gh-mcp",
        "args": ["--stdio"],
        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
    }
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    assert run("apply", "--global", "--yes") == 0
    rows = [r for r in engine.status(kit_paths.resolve()) if r["item"] == "mcp:github"]
    assert any(r["preexisting"] for r in rows)
    assert run("purge", "--global", "--yes") == 0
    assert "github" in json.loads(claude_json.read_text())["mcpServers"]


def test_interrupted_apply_rolls_forward(env: dict[str, Path], capsys) -> None:
    baseline = agent_files(env)
    paths = kit_paths.resolve()
    warehouse = Warehouse(env["warehouse"])
    compiled = compile_desired(desired_user(warehouse), paths, warehouse.root)
    plan = engine.plan("user", compiled.effects, paths)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(plan, paths, crash_after=3)
    assert agent_files(env) != baseline
    assert run("status") == 1
    assert "interrupted" in capsys.readouterr().out
    assert run("apply", "--global", "--dry-run") == 2  # dry runs never recover
    assert run("recover") == 0
    assert run("status") == 0
    assert run("apply", "--global", "--dry-run") == 0
    assert "Nothing to change." in capsys.readouterr().out
    assert run("purge", "--global", "--yes") == 0
    assert agent_files(env) == baseline


def test_interrupted_apply_rolls_back(env: dict[str, Path]) -> None:
    baseline = agent_files(env)
    paths = kit_paths.resolve()
    warehouse = Warehouse(env["warehouse"])
    compiled = compile_desired(desired_user(warehouse), paths, warehouse.root)
    plan = engine.plan("user", compiled.effects, paths)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(plan, paths, crash_after=4)
    assert run("recover", "--rollback") == 0
    assert agent_files(env) == baseline
    assert engine.Ledger(paths).records == {}


def test_recovery_refuses_when_files_changed(env: dict[str, Path]) -> None:
    paths = kit_paths.resolve()
    warehouse = Warehouse(env["warehouse"])
    compiled = compile_desired(desired_user(warehouse), paths, warehouse.root)
    plan = engine.plan("user", compiled.effects, paths)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(plan, paths, crash_after=1)
    journal = engine.pending(paths)
    assert journal is not None
    untouched = Path(journal["writes"][-1]["path"])
    untouched.parent.mkdir(parents=True, exist_ok=True)
    untouched.write_text("someone else\n")
    assert run("recover") == 2


def test_concurrent_apply_is_refused(env: dict[str, Path], capsys) -> None:
    lock = kit_paths.resolve().kit_state_dir / "apply.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("123\n")
    assert run("apply", "--global", "--yes") == 2
    assert "Another kitctl apply" in capsys.readouterr().err


def test_corrupted_ledger(env: dict[str, Path], capsys) -> None:
    ledger = kit_paths.resolve().kit_state_dir / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{not json")
    assert run("apply", "--global", "--dry-run") == 2
    assert "unreadable" in capsys.readouterr().err
    assert run("status") == 2


def test_symlinked_target_is_a_conflict(env: dict[str, Path], capsys, tmp_path: Path) -> None:
    from tests.conftest import link

    real = tmp_path / "elsewhere.json"
    real.write_text("{}\n")
    settings = env["home"] / ".claude/settings.json"
    settings.unlink()
    link(settings, real)
    assert run("apply", "--global", "--yes") == 1
    assert "symlink" in capsys.readouterr().out
    assert real.read_text() == "{}\n"


def test_duplicate_markers_are_a_conflict(env: dict[str, Path], capsys) -> None:
    marker = "<!-- kitctl:begin rules:style -->\n"
    write(env["repo"] / "AGENTS.md", f"# Repo\n{marker}a\n{marker}b\n")
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 1
    assert "duplicate or unbalanced" in capsys.readouterr().out


def test_invalid_existing_json_is_a_conflict(env: dict[str, Path], capsys) -> None:
    write(env["repo"] / ".mcp.json", "{oops")
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_gaps_fail_the_plan(env: dict[str, Path], capsys) -> None:
    write(
        env["repo"] / "kit.toml",
        'schema_version = 1\nloadouts = ["web"]\n[mcp.docs]\nenabled_tools = ["search"]\n',
    )
    before = agent_files(env)
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 1
    assert "strict tool allowlist" in capsys.readouterr().err
    assert agent_files(env) == before
    write(
        env["repo"] / "kit.toml",
        'schema_version = 1\nloadouts = ["web"]\nharnesses = '
        '["codex"]\n[mcp.docs]\nenabled_tools = ["search"]\n',
    )
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 0
    assert 'enabled_tools = ["search"]' in (env["repo"] / ".codex/config.toml").read_text()
    assert not (env["repo"] / ".mcp.json").exists()


def test_disabled_tools_become_claude_deny_rules(env: dict[str, Path]) -> None:
    write(
        env["repo"] / "kit.toml",
        'schema_version = 1\nloadouts = ["web"]\n[mcp.docs]\ndisabled_tools = ["delete"]\n',
    )
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 0
    settings = json.loads((env["repo"] / ".claude/settings.json").read_text())
    assert settings["permissions"]["deny"] == ["mcp__docs__delete"]
    assert 'disabled_tools = ["delete"]' in (env["repo"] / ".codex/config.toml").read_text()


def test_desired_errors(env: dict[str, Path]) -> None:
    warehouse = Warehouse(env["warehouse"])
    repo = env["repo"]
    cases = {
        'schema_version = 1\nloadouts = ["nope"]\n': "No loadout named",
        'schema_version = 1\nloadouts = ["core"]\n': "core applies at user scope",
        "schema_version = 1\nextra = 1\n": "unknown fields",
        'schema_version = 1\n[remove]\nskills = ["writer"]\n': "comes from core",
        'schema_version = 1\n[add]\nskills = ["../x"]\n': "no skill named",
        'schema_version = 1\n[mcp.github]\ndisabled_tools = ["a"]\n': "not in core",
    }
    for text, message in cases.items():
        write(repo / "kit.toml", text)
        with pytest.raises(DesiredError, match=message):
            desired_repo(warehouse, repo)
    write(
        env["warehouse"] / "loadouts/web.toml",
        'schema_version = 1\nname = "web"\nextends = ["web"]\n',
    )
    with pytest.raises(DesiredError, match="validation errors"):
        Warehouse(env["warehouse"])


def test_repo_items_already_in_core_are_skipped(env: dict[str, Path]) -> None:
    write(
        env["repo"] / "kit.toml",
        'schema_version = 1\nloadouts = ["web"]\n[add]\nskills = ["writer"]\n',
    )
    wanted = desired_repo(Warehouse(env["warehouse"]), env["repo"])
    assert "skill:writer" not in {item.ref for item in wanted.items}
    assert any("already installed at user scope" in note for note in wanted.notes)


def test_codex_translation_gaps() -> None:
    table, problems = codex_server(
        "mcp:x", {"type": "stdio", "command": "x", "env": {"A": "${B}", "C": "plain"}}
    )
    assert table["env"] == {"C": "plain"} and "can't be expressed" in problems[0]
    table, problems = codex_server(
        "mcp:y", {"type": "http", "url": "https://h/mcp", "headers": {"X-Key": "${KEY}"}}
    )
    assert table["env_http_headers"] == {"X-Key": "KEY"} and not problems
    _, problems = codex_server("mcp:z", {"type": "sse", "url": "https://h/sse"})
    assert "SSE" in problems[0]


def test_eject_leaves_plain_native_config(env: dict[str, Path], tmp_path: Path) -> None:
    assert run("apply", "--global", "--yes") == 0
    assert run("apply", "--repo", str(env["repo"]), "--yes") == 0
    assert run("eject", "--yes") == 0
    repo, home = env["repo"], env["home"]
    agents = (repo / "AGENTS.md").read_text()
    assert "kitctl:" not in agents and "Prefer short sentences." in agents
    assert not (repo / "kit.lock").exists()
    assert engine.Ledger(kit_paths.resolve()).records == {}
    env["warehouse"].rename(tmp_path / "moved-away")
    assert (home / ".claude/skills/writer/SKILL.md").is_file()
    for path in (
        home / ".claude/settings.json",
        home / ".claude/.claude.json",
        home / ".codex/config.toml",
        repo / ".mcp.json",
    ):
        text = path.read_text()
        assert "/kits/" not in text and "kitctl reconcile" not in text
    assert run("status") == 0
