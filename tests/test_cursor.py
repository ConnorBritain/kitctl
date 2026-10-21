"""Cursor as a third harness: resolve, sandbox apply/off/eject, intent and inventory."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from kitctl import engine
from kitctl.cli import main
from kitctl.doctor import doctor
from kitctl.forge import check_brief
from kitctl.inventory import inventory
from kitctl.resolve import resolve
from kitctl.validate import validate

FIXTURES = Path(__file__).parent / "fixtures"
WAREHOUSE = FIXTURES / "warehouse"
RULE = ".cursor/rules/kit-cursor-kit.mdc"

PRIMITIVES = {
    "mcp/local.yaml": "kind: mcp\nname: local\ncommand: example-server\nargs: [--quiet]\n"
    "env: {API_KEY: {env: API_KEY}, OTHER: {env: RENAMED}}\n",
    "mcp/remote.yaml": "kind: mcp\nname: remote\ntransport: http\n"
    "url: https://mcp.example.invalid/mcp\nheaders: {Authorization: {env: REMOTE_TOKEN}}\n",
    "hooks/lint-on-edit.yaml": "kind: hook\nname: lint-on-edit\nevent: PostToolUse\n"
    'matcher: "Edit|Write"\ncommand: [node, scripts/lint.mjs]\ntimeout: 5\n',
    "hooks/notify-stop.yaml": 'kind: hook\nname: notify-stop\nevent: Stop\nmatcher: ""\n'
    "command: [say, done]\n",
    "hooks/guard-bash.yaml": "kind: hook\nname: guard-bash\nevent: PreToolUse\nmatcher: Bash\n"
    "command: [node, scripts/guard.mjs]\n",
    "plugins/own.yaml": "kind: plugin\nname: own\nharnesses: [cursor]\n",
    "plugins/ext.yaml": "kind: plugin\nname: ext\nexternal: true\nharnesses: {cursor: {id: ext}}\n",
    "skills/noisy.yaml": "kind: skill\nname: noisy\nexternal: true\nharnesses: [cursor]\n",
    "skills/helper.yaml": "kind: skill\nname: helper\ndescription: Helps.\n",
    "skills/helper/SKILL.md": "---\nname: helper\n---\nHelp.\n",
}
LOADOUT = """schema_version: 2
name: cursor-kit
description: Exercises every Cursor branch.
mcp:
  local: { primitive: mcp:local }
  remote: { primitive: mcp:remote, enabled_tools: [search] }
hooks: [hook:lint-on-edit, hook:notify-stop, hook:guard-bash]
plugins: [plugin:own]
skills: [skill:helper]
disable: [plugin:ext, skill:noisy]
instructions: |
  Keep changes small.
"""


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "warehouse"
    for name, text in PRIMITIVES.items():
        path = root / "primitives" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if name.endswith(".md") else "schema_version: 2\n" + text)
    (root / "loadouts").mkdir()
    (root / "loadouts" / "cursor-kit.yaml").write_text(LOADOUT)
    return root


@pytest.fixture
def apply_warehouse(warehouse: Path) -> Path:
    # Kit does not copy skill directories yet on any harness (the engine reports it as a
    # conflict), so the apply round trips use the same loadout without its skill.
    path = warehouse / "loadouts" / "cursor-kit.yaml"
    path.write_text(LOADOUT.replace("skills: [skill:helper]\n", ""))
    return warehouse


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "sandbox" / "site"
    (project / ".claude").mkdir(parents=True)
    (project / "CLAUDE.md").write_bytes(b"# Project\n")
    (project / ".claude/settings.json").write_bytes(b'{\n  "model": "opus"\n}\n')
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path / "sandbox"))
    return project.resolve()


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def intent(*loadouts: str, harnesses: tuple[str, ...] = ("cursor",)) -> dict[str, Any]:
    return {"schema_version": 1, "loadouts": list(loadouts), "harnesses": list(harnesses)}


def apply(repo: Path, warehouse: Path, wanted: dict[str, Any]) -> engine.Plan:
    plan = engine.make_plan(repo, warehouse, wanted)
    engine.apply_plan(plan)
    return plan


def gaps(result: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(g["subject"], g["capability"]): g["severity"] for g in result["gaps"]}


def test_warehouse_validates_for_three_harnesses(warehouse: Path) -> None:
    report = validate(warehouse)
    assert [p for p in report["problems"] if p["severity"] == "error"] == []
    assert report["resolutions"] == 3


def test_mcp_writes_env_references_never_values(warehouse: Path) -> None:
    result = resolve(warehouse, "cursor-kit", "cursor")
    servers = {e["key"]: e["value"] for e in result["effects"] if e["target"] == ".cursor/mcp.json"}
    assert servers["/mcpServers/local"] == {
        "command": "example-server",
        "args": ["--quiet"],
        "env": {"API_KEY": "${env:API_KEY}", "OTHER": "${env:RENAMED}"},
    }
    assert servers["/mcpServers/remote"] == {
        "url": "https://mcp.example.invalid/mcp",
        "headers": {"Authorization": "${env:REMOTE_TOKEN}"},
    }
    found = gaps(result)
    assert found[("mcp:local", "mcp_project_file")] == "unverified"
    assert found[("mcp:local", "mcp_env_reference")] == "unverified"
    assert found[("mcp:remote", "mcp_tool_filter")] == "unsupported"
    assert ("mcp:local", "mcp_env_rename") not in found  # ${env:...} can rename
    assert {e["evidence"] for e in result["effects"]} == {"documented"}


def test_hooks_map_stop_and_edit_events_and_gap_the_rest(warehouse: Path) -> None:
    result = resolve(warehouse, "cursor-kit", "cursor")
    hooks = [
        (e["key"], e["value"]) for e in result["effects"] if e["target"] == ".cursor/hooks.json"
    ]
    assert hooks == [
        ("/hooks/afterFileEdit/-", {"command": "node scripts/lint.mjs"}),
        ("/hooks/stop/-", {"command": "say done"}),
        ("/version", 1),
    ]
    found = gaps(result)
    assert found[("hook:guard-bash", "hook_event")] == "unsupported"
    assert found[("hook:lint-on-edit", "hooks_project_file")] == "unverified"
    assert found[("hook:lint-on-edit", "hook_timeout")] == "unverified"
    assert "The guard-bash hook does not run in Cursor" in result["instructions"]["text"]


def test_plugins_skills_and_disables_are_gaps_with_soft_rules(warehouse: Path) -> None:
    result = resolve(warehouse, "cursor-kit", "cursor")
    found = gaps(result)
    assert found[("plugin:own", "plugin_enable")] == "manual-step"
    assert found[("plugin:ext", "plugin_disable")] == "unsupported"
    assert found[("skill:noisy", "skill_disable")] == "unsupported"
    assert found[("skill:helper", "skill_dir")] == "unverified"
    assert any(e["target"] == ".cursor/skills/helper/" for e in result["effects"])
    # Whether Cursor gates .cursor/ on trust is unknown: flagged, never claimed either way.
    assert found[("loadout:cursor-kit", "project_trust")] == "unverified"
    text = result["instructions"]["text"]
    assert "- Do not use ext in this repository." in text
    assert "- Do not use noisy in this repository." in text


def test_instructions_become_a_whole_rule_file(warehouse: Path) -> None:
    block = resolve(warehouse, "cursor-kit", "cursor")["instructions"]
    assert block["target"] == RULE and block["format"] == "file"
    assert block["text"].startswith(
        "---\ndescription: Working rules from the cursor-kit loadout\nalwaysApply: true\n---\n\n"
        "Keep changes small.\n"
    )
    assert "kit:begin" not in block["text"]
    claude = resolve(warehouse, "cursor-kit", "claude")["instructions"]
    assert (claude["target"], claude["format"]) == ("CLAUDE.md", "markdown-block")


def test_brand_studio_marketplace_is_a_manual_step_when_cursor_is_named(tmp_path: Path) -> None:
    root = tmp_path / "wh"
    shutil.copytree(WAREHOUSE, root)
    market = root / "primitives/marketplaces/brand-builder.yaml"
    market.write_text(market.read_text().replace("  codex: {", "  cursor: {}\n  codex: {"))
    result = resolve(root, "brand-studio", "cursor")
    gap = next(g for g in result["gaps"] if g["capability"] == "marketplace_file")
    assert gap["severity"] == "manual-step"
    assert "brand-builder.git" in gap["remedy"]
    assert not any(e["target"].startswith(".claude") for e in result["effects"])


def test_sandbox_apply_repeat_off_restores_baseline(repo: Path, apply_warehouse: Path) -> None:
    baseline = tree(repo)
    plan = apply(repo, apply_warehouse, intent("cursor-kit"))
    assert set(plan.changes) == {
        ".cursor/hooks.json",
        ".cursor/mcp.json",
        RULE,
        "kit.lock",
        "kit.yaml",
    }
    rule = (repo / RULE).read_text()
    assert rule.startswith("---\ndescription:") and "alwaysApply: true" in rule
    hooks = json.loads((repo / ".cursor/hooks.json").read_text())
    assert hooks == {
        "version": 1,
        "hooks": {
            "afterFileEdit": [{"command": "node scripts/lint.mjs"}],
            "stop": [{"command": "say done"}],
        },
    }
    mcp = json.loads((repo / ".cursor/mcp.json").read_text())["mcpServers"]
    assert mcp["local"]["env"]["API_KEY"] == "${env:API_KEY}"
    assert engine.read_intent(repo)["harnesses"] == ["cursor"]
    assert engine.make_plan(repo, apply_warehouse, engine.read_intent(repo)).changes == {}
    apply(repo, apply_warehouse, intent(harnesses=("cursor",)))
    assert tree(repo) == baseline
    assert not (repo / ".cursor").exists()


def test_brand_studio_on_all_three_harnesses_round_trips(repo: Path) -> None:
    baseline = tree(repo)
    both = ("claude", "codex", "cursor")
    plan = apply(repo, WAREHOUSE, intent("brand-studio", harnesses=both))
    assert {".cursor/mcp.json", ".cursor/rules/kit-brand-studio.mdc", "CLAUDE.md"} <= set(
        plan.changes
    )
    apply(repo, WAREHOUSE, intent(harnesses=both))
    assert tree(repo) == baseline


def test_eject_keeps_cursor_files_without_kit_state(repo: Path, apply_warehouse: Path) -> None:
    apply(repo, apply_warehouse, intent("cursor-kit"))
    rule = (repo / RULE).read_bytes()
    touched = engine.eject(repo)
    assert "kit.lock" in touched and RULE not in touched
    assert (repo / RULE).read_bytes() == rule and b"kit:" not in rule
    assert json.loads((repo / ".cursor/hooks.json").read_text())["version"] == 1
    assert (repo / ".cursor/mcp.json").is_file()
    assert not (repo / "kit.lock").exists() and not (repo / "kit.yaml").exists()
    assert not (repo / ".kit").exists()


def test_existing_rule_file_is_never_taken_over(repo: Path, warehouse: Path) -> None:
    path = repo / RULE
    path.parent.mkdir(parents=True)
    path.write_text("---\nalwaysApply: true\n---\n\nMine.\n")
    plan = engine.make_plan(repo, warehouse, intent("cursor-kit"))
    assert any(c.startswith(f"unmanaged: {RULE}#kit:cursor-kit") for c in plan.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(plan)
    assert path.read_text().endswith("Mine.\n")


def test_edited_rule_file_is_drift_not_removed(repo: Path, apply_warehouse: Path) -> None:
    apply(repo, apply_warehouse, intent("cursor-kit"))
    (repo / RULE).write_text((repo / RULE).read_text() + "My note.\n")
    plan = engine.make_plan(repo, apply_warehouse, intent(harnesses=("cursor",)))
    assert any(c.startswith(f"drift: {RULE}#kit:cursor-kit") for c in plan.conflicts)


def test_live_apply_is_still_refused(
    repo: Path, warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(engine.SANDBOX_ENV)
    plan = engine.make_plan(repo, warehouse, intent("cursor-kit"))
    with pytest.raises(engine.EngineError, match="sandbox"):
        engine.apply_plan(plan)
    assert not (repo / ".cursor").exists()


def test_intent_accepts_cursor_and_keeps_the_default(repo: Path) -> None:
    assert engine.read_intent(repo)["harnesses"] == ["claude", "codex"]
    (repo / "kit.yaml").write_text("schema_version: 1\nloadouts: []\nharnesses: [claude, cursor]\n")
    assert engine.read_intent(repo)["harnesses"] == ["claude", "cursor"]
    (repo / "kit.yaml").write_text("schema_version: 1\nloadouts: []\nharnesses: [vim]\n")
    with pytest.raises(engine.EngineError, match="claude, codex, cursor"):
        engine.read_intent(repo)


def test_forge_brief_and_cli_accept_cursor(capsys: pytest.CaptureFixture[str]) -> None:
    brief = {"schema_version": 1, "purpose": "p", "harnesses": ["cursor"]}
    assert not [p for p in check_brief(brief) if "harnesses" in p]
    assert [p for p in check_brief({**brief, "harnesses": ["vim"]}) if "harnesses" in p]
    args = ["resolve", "brand-studio", "--harness", "cursor", "--warehouse", str(WAREHOUSE)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert ".cursor/rules/kit-brand-studio.mdc  managed block kit:brand-studio" in out
    assert "gap [manual-step] plugin:brand-builder" in out


def test_inventory_reads_cursor_files_redacted_and_read_only(sandbox: Path) -> None:
    cursor = sandbox / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": "example-server",
                        "args": ["--token", "fixture-secret-value"],
                        "env": {"API_KEY": "${env:API_KEY}", "LEAK": "fixture-secret-env"},
                    },
                    "remote": {
                        "url": "https://user:pw@mcp.example.invalid/mcp?key=fixture-secret-q",
                        "headers": {"Authorization": "${env:TOKEN}"},
                    },
                }
            }
        )
    )
    (cursor / "hooks.json").write_text(
        '{"version": 1, "hooks": {"stop": [{"command": "say done", "loop_limit": 3}],'
        ' "subagentStop": [{"command": "log", "matcher": "^advisor$"}]}}'
    )
    before = tree(sandbox)
    report = inventory([sandbox])
    dumped = json.dumps(report)
    assert "fixture-secret" not in dumped and "pw@" not in dumped
    servers = {s["id"]: s for s in report["native"]["mcp_servers"] if s["harness"] == "cursor"}
    assert servers["local"]["env"] == {"API_KEY": "reference", "LEAK": "literal"}
    assert servers["local"]["args"] == ["--token", "<redacted>"]
    assert servers["remote"]["transport"] == "http"
    assert servers["remote"]["headers"] == {"Authorization": "reference"}
    hooks = {(h["event"], h["matcher"]) for h in report["native"]["hooks"]}
    assert hooks == {("stop", ""), ("subagentStop", "^advisor$")}
    assert {".cursor/mcp.json", ".cursor/hooks.json"} <= {e["name"] for e in report["entries"]}
    assert tree(sandbox) == before


def test_doctor_checks_cursor_mcp_launch(sandbox: Path, warehouse: Path) -> None:
    report = doctor(warehouse, [sandbox], "cursor-kit", ("cursor",), runner=lambda _: (0, ""))
    launch = [f for f in report["findings"] if f["check"] == "mcp-launch"]
    assert [f["subject"] for f in launch] == ["cursor mcp local"]
    assert "cursor" in report["harnesses"]
