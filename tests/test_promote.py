"""kitctl promote: triage decisions become primitives and loadouts; adopt hook:<name>."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from kitctl import adopt, fs, triage
from kitctl.cli import main
from kitctl.inventory import inventory
from kitctl.validate import validate
from tests.kits import snapshot, write


def decision(kind: str, harness: str, name: str, verdict: str, loadout: str = "") -> dict:
    return {"kind": kind, "harness": harness, "name": name, "decision": verdict,
            "loadout": loadout, "canonical": "", "reason": "test", "proposed": verdict,
            "source": "test", "decided_at": "2026-10-19T00:00:00Z", "locations": []}  # fmt: skip


@pytest.fixture
def warehouse(harnesses: Path, tmp_path: Path) -> Path:
    root = tmp_path / "home/kits"
    root.mkdir(parents=True)
    fs.allow(root)
    rows = [
        decision("agent", "claude", "reviewer", "core"),
        decision("mcp_server", "claude", "github", "core"),
        decision("plugin", "claude", "deploy-tools@demo-market", "core"),
        decision("hook", "claude", "SessionStart:*:session-check", "core"),
        decision("skill", "claude", "writer", "loadout", "web"),
        decision("command", "claude", "ship", "retire"),
    ]
    triage.save(root, {(r["kind"], r["harness"], r["name"]): r for r in rows})
    return root


def test_promote_dry_run_writes_nothing(warehouse: Path, harnesses: Path, capsys) -> None:
    before = snapshot(warehouse, harnesses)
    assert main(["promote", "--dry-run", "--root", str(harnesses)]) == 0
    out = capsys.readouterr().out
    assert "would adopt" in out and "loadouts/core.toml" in out
    assert snapshot(warehouse, harnesses) == before


def test_promote_builds_loadouts(warehouse: Path, harnesses: Path, capsys) -> None:
    repo = harnesses
    assert main(["promote", "--write-kit-files", "--root", str(repo)]) == 0
    captured = capsys.readouterr()
    core = tomllib.loads((warehouse / "loadouts/core.toml").read_text())
    assert core["agents"] == ["reviewer"] and core["mcp"] == ["github"]
    assert core["plugins"] == ["deploy-tools@demo-market"]
    assert "hooks" not in core  # literal API_KEY in the command: refused, reported
    assert "session-check" in captured.err and "literal" in captured.err
    web = tomllib.loads((warehouse / "loadouts/web.toml").read_text())
    assert web["skills"] == ["writer"]
    assert tomllib.loads((repo / "kit.toml").read_text())["loadouts"] == ["web"]
    assert (warehouse / "primitives/skills/writer/files/SKILL.md").is_file()
    server = json.loads((warehouse / "primitives/mcp/github/server.json").read_text())
    assert "CANARY" not in json.dumps(server)
    assert not [f for f in validate(warehouse)["findings"] if f["severity"] == "error"]
    assert not (warehouse / "primitives/commands/ship").exists()  # retire: not promoted

    # Hand edits survive and re-running adds nothing.
    core_file = warehouse / "loadouts/core.toml"
    core_file.write_text("# my notes\n" + core_file.read_text())
    before = snapshot(warehouse)
    assert main(["promote", "--root", str(repo)]) == 0
    assert snapshot(warehouse) == before

    # The result is applyable: identical entries are adopted, the literal-credential
    # server is a migration conflict, and nothing is written in a dry run.
    capsys.readouterr()
    assert main(["apply", "--global", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "adopt   agent:reviewer" in out and "literal credentials" in out


def test_adopt_hook_with_script(harnesses: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    script = home / "bin/check.sh"
    write(script, "#!/bin/sh\nexit 0\n", mode=0o755)
    settings = home / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"].append(
        {
            "matcher": "Edit",
            "hooks": [{"type": "command", "command": "~/bin/check.sh --fast", "timeout": 5}],
        }  # fmt: skip
    )
    settings.write_text(json.dumps(data))
    warehouse = home / "kits"
    warehouse.mkdir()
    fs.allow(warehouse)
    entries = inventory([harnesses])["entries"]
    result = adopt.adopt_hook("PreToolUse:Edit:check.sh", entries, warehouse, "primitives")
    target = Path(result["target"])
    meta = tomllib.loads((target / "primitive.toml").read_text())
    assert meta["hook"] == {"event": "PreToolUse", "matcher": "Edit", "timeout": 5,
                            "script": "check.sh", "command": "{script} --fast"}  # fmt: skip
    assert (target / "files/check.sh").read_text().startswith("#!/bin/sh")
    assert not [f for f in validate(warehouse)["findings"] if f["severity"] == "error"]
    with pytest.raises(adopt.AdoptError, match="literal"):
        adopt.adopt_hook("SessionStart:*:session-check", entries, warehouse, "primitives")
