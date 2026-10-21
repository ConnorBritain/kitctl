"""Acceptance for plan/apply/use/off/status/eject (design.md), run inside a sandbox."""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from kitctl import engine
from kitctl.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
WAREHOUSE = FIXTURES / "warehouse"

BASELINE = {
    "CLAUDE.md": b"# Project\n\nExisting notes.\n",
    ".claude/settings.json": b'{\n  "permissions": {\n    "allow": ["Bash(ls)"]\n  }\n}\n',
    ".codex/config.toml": b'# my codex settings\nmodel = "gpt-5"\n\n[mcp_servers.docs]\n'
    b'command = "docs-mcp"  # keep me\n',
}


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def dirs(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "work space" / "site"  # a space in the path, as on many Windows homes
    for name, data in BASELINE.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path))
    return project.resolve()


def use(repo: Path, target: str, warehouse: Path = WAREHOUSE) -> engine.Plan:
    plan = engine.make_plan(repo, warehouse, engine.with_loadout(engine.read_intent(repo), target))
    engine.apply_plan(plan)
    return plan


def off(repo: Path, name: str, warehouse: Path = WAREHOUSE) -> engine.Plan:
    plan = engine.make_plan(repo, warehouse, engine.without_loadout(engine.read_intent(repo), name))
    engine.apply_plan(plan)
    return plan


def replan(repo: Path, warehouse: Path = WAREHOUSE) -> engine.Plan:
    return engine.make_plan(repo, warehouse, engine.read_intent(repo))


def test_use_repeat_off_restores_baseline_byte_for_byte(repo: Path) -> None:
    baseline, baseline_dirs = tree(repo), dirs(repo)
    plan = use(repo, "brand-studio")
    assert set(plan.changes) == {
        ".claude/settings.json",
        ".codex/config.toml",
        ".mcp.json",
        "AGENTS.md",
        "CLAUDE.md",
        "kit.lock",
        "kit.yaml",
    }
    settings = json.loads((repo / ".claude/settings.json").read_text())
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    assert settings["enabledPlugins"]["brand-builder@brand-builder"] is True
    toml = (repo / ".codex/config.toml").read_text()
    assert toml.startswith("# my codex settings\n") and "# keep me" in toml
    assert "[mcp_servers]\n" not in toml and "[mcp_servers.chrome-devtools]" in toml
    assert "<!-- kit:begin brand-studio -->" in (repo / "CLAUDE.md").read_text()
    applied = tree(repo)
    assert replan(repo).changes == {}  # repeat apply: no diff
    assert engine.apply_plan(replan(repo)) == []
    assert tree(repo) == applied
    off(repo, "brand-studio")
    assert tree(repo) == baseline
    assert dirs(repo) == baseline_dirs


def test_profile_switch_applies_only_the_delta_and_back(repo: Path) -> None:
    import tomllib

    def native(root: Path) -> dict[str, Any]:
        files = {k: v for k, v in tree(root).items() if not k.startswith(("kit.", ".kit/"))}
        # Re-added TOML tables land at the end of the file; compare TOML by meaning.
        files[".codex/config.toml"] = tomllib.loads(files[".codex/config.toml"].decode())
        return files

    use(repo, "brand-studio")
    full = native(repo)
    plan = use(repo, "brand-studio:audit")
    actions = {(op["action"], op["id"]) for op in plan.ops}
    assert ("remove", ".mcp.json#/mcpServers/leonardo") in actions
    assert ("remove", ".codex/config.toml#mcp_servers.leonardo") in actions
    assert not any(action == "add" for action, ident in actions if "chrome-devtools" in ident)
    assert ("reattribute", ".mcp.json#/mcpServers/chrome-devtools") in actions
    removed = {i for a, i in actions if a == "remove"}
    assert all("leonardo" in i or "kit:brand-studio" in i for i in removed)
    assert "Audit only" in (repo / "CLAUDE.md").read_text()
    lock = json.loads((repo / "kit.lock").read_text())["body"]
    assert lock["loadouts"] == [{"loadout": "brand-studio", "profile": "audit"}]
    assert all(
        e["owners"] == [{"loadout": "brand-studio", "profile": "audit"}] for e in lock["entries"]
    )
    use(repo, "brand-studio")
    assert native(repo) == full


def test_unrelated_user_edits_survive_off(repo: Path) -> None:
    use(repo, "brand-studio")
    settings = repo / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["model"] = "opus"
    settings.write_text(json.dumps(data, indent=2) + "\n")
    config = repo / ".codex/config.toml"
    config.write_text(
        "# added later\n" + config.read_text() + '\n[profiles.fast]\nmodel = "mini"\n'
    )
    claude_md = repo / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + "\nUser line after the block.\n")
    off(repo, "brand-studio")
    after = json.loads(settings.read_text())
    assert after == {"permissions": {"allow": ["Bash(ls)"]}, "model": "opus"}
    toml = config.read_text()
    assert "# added later" in toml and "[profiles.fast]" in toml and "# keep me" in toml
    assert "chrome-devtools" not in toml and "plugins" not in toml
    text = claude_md.read_text()
    assert "kit:begin" not in text and "User line after the block." in text
    assert text.startswith("# Project\n\nExisting notes.\n")
    assert not (repo / ".mcp.json").exists() and not (repo / "kit.lock").exists()


def test_interrupted_multi_file_write_recovers_from_journal(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean = tmp_path / "clean"
    shutil.copytree(repo, clean)
    use(clean, "brand-studio")
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    real_replace = os.replace
    calls = {"n": 0}

    def crash_after_two(src: Any, dst: Any) -> None:
        if ".kit-stage-" not in str(src):
            return real_replace(src, dst)  # preimages and the journal itself
        calls["n"] += 1
        if calls["n"] > 2:
            raise OSError("simulated power loss")
        return real_replace(src, dst)

    monkeypatch.setattr(engine.os, "replace", crash_after_two)
    with pytest.raises(OSError, match="power loss"):
        engine.apply_plan(plan)
    monkeypatch.setattr(engine.os, "replace", real_replace)
    assert (repo / engine.JOURNAL).exists()
    with pytest.raises(engine.EngineError, match="interrupted"):
        replan(repo)
    (repo / engine.LOCKFILE).unlink(missing_ok=True)  # a crashed process may leave it
    assert engine.recover(repo) is True
    assert not (repo / engine.JOURNAL).exists()
    strip = {".kit/"}
    assert {k: v for k, v in tree(repo).items() if not k.startswith(tuple(strip))} == {
        k: v for k, v in tree(clean).items() if not k.startswith(tuple(strip))
    }
    assert replan(repo).changes == {}
    assert not list(repo.rglob("*.kit-stage-*"))


def test_drift_is_a_conflict_not_a_takeover(repo: Path) -> None:
    use(repo, "brand-studio")
    mcp = repo / ".mcp.json"
    data = json.loads(mcp.read_text())
    data["mcpServers"]["chrome-devtools"]["args"].append("--user-flag")
    mcp.write_text(json.dumps(data, indent=2) + "\n")
    before = tree(repo)
    plan = replan(repo)
    assert any(c.startswith("drift: .mcp.json#/mcpServers/chrome-devtools") for c in plan.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(plan)
    with pytest.raises(engine.ConflictError):
        off(repo, "brand-studio")
    assert tree(repo) == before
    report = engine.status(repo, WAREHOUSE)
    states = {e["id"]: e["state"] for e in report["entries"]}
    assert states[".mcp.json#/mcpServers/chrome-devtools"] == "drift"


def test_unmanaged_value_is_a_conflict_and_identical_value_is_adopted(repo: Path) -> None:
    settings = repo / ".claude/settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {"brand-builder@brand-builder": False}}))
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    assert any(
        c.startswith("unmanaged: .claude/settings.json#/enabledPlugins/brand-builder")
        for c in plan.conflicts
    )
    settings.write_text(json.dumps({"enabledPlugins": {"brand-builder@brand-builder": True}}))
    baseline = tree(repo)
    plan = use(repo, "brand-studio")
    assert ("adopt", ".claude/settings.json#/enabledPlugins/brand-builder@brand-builder") in {
        (op["action"], op["id"]) for op in plan.ops
    }
    off(repo, "brand-studio")
    assert tree(repo) == baseline  # Kit never owned the adopted value, so it stays.


def test_off_touches_only_that_loadout(repo: Path, tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    shutil.copytree(WAREHOUSE, warehouse)
    (warehouse / "loadouts/notes.yaml").write_text(
        "schema_version: 2\nname: notes\n"
        "mcp:\n  chrome-devtools: {primitive: mcp:chrome-devtools}\n"
        "instructions: Keep notes short.\n"
    )
    use(repo, "brand-studio", warehouse)
    use(repo, "notes", warehouse)
    lock = json.loads((repo / "kit.lock").read_text())["body"]
    shared = next(e for e in lock["entries"] if e["key"] == "/mcpServers/chrome-devtools")
    assert {o["loadout"] for o in shared["owners"]} == {"brand-studio", "notes"}
    off(repo, "brand-studio", warehouse)
    mcp = json.loads((repo / ".mcp.json").read_text())["mcpServers"]
    assert set(mcp) == {"chrome-devtools"}
    text = (repo / "CLAUDE.md").read_text()
    assert "kit:begin notes" in text and "kit:begin brand-studio" not in text
    settings = json.loads((repo / ".claude/settings.json").read_text())
    assert settings == {"permissions": {"allow": ["Bash(ls)"]}}
    assert engine.read_intent(repo)["loadouts"] == ["notes"]


def test_eject_leaves_native_files_without_kit(repo: Path) -> None:
    use(repo, "brand-studio")
    native = {
        k: v
        for k, v in tree(repo).items()
        if k in {".mcp.json", ".codex/config.toml", ".claude/settings.json"}
    }
    engine.eject(repo)
    files = tree(repo)
    assert not {"kit.lock", "kit.yaml"} & set(files) and not (repo / ".kit").exists()
    for name, data in native.items():
        assert files[name] == data
    for name, data in files.items():
        assert b"kit:" not in data and b"kitctl" not in data, name
    text = (repo / "CLAUDE.md").read_text()
    assert "This repo uses the brand-studio loadout." in text
    json.loads(files[".mcp.json"])
    assert main(["plan", "--repo", str(repo), "--warehouse", str(WAREHOUSE)]) == 0  # nothing active


def test_crlf_files_keep_their_line_endings(repo: Path) -> None:
    (repo / "CLAUDE.md").write_bytes(b"# Windows notes\r\n\r\nLine.\r\n")
    (repo / ".claude/settings.json").write_bytes(b'{\r\n  "model": "x"\r\n}\r\n')
    baseline = tree(repo)
    use(repo, "brand-studio")
    assert b"\r\n<!-- kit:begin brand-studio -->\r\n" in (repo / "CLAUDE.md").read_bytes()
    assert b"\n" not in (repo / ".claude/settings.json").read_bytes().replace(b"\r\n", b"")
    lock = json.loads((repo / "kit.lock").read_text())["body"]
    assert all("\\" not in e["target"] for e in lock["entries"])
    off(repo, "brand-studio")
    assert tree(repo) == baseline


def test_duplicate_markers_are_a_conflict(repo: Path) -> None:
    block = "<!-- kit:begin brand-studio -->\nx\n<!-- kit:end brand-studio -->\n"
    (repo / "CLAUDE.md").write_text("# P\n" + block + block)
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    assert any("duplicate kit:brand-studio markers" in c for c in plan.conflicts)


def test_corrupted_ledger_is_refused(repo: Path) -> None:
    use(repo, "brand-studio")
    lock = repo / "kit.lock"
    lock.write_text(lock.read_text().replace('"value": true', '"value": false', 1))
    with pytest.raises(engine.EngineError, match="corrupted"):
        replan(repo)
    lock.write_text("{not json")
    with pytest.raises(engine.EngineError, match="corrupted"):
        engine.status(repo, WAREHOUSE)


def test_concurrent_runs_and_edits_between_plan_and_apply(repo: Path) -> None:
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    (repo / ".kit").mkdir()
    (repo / engine.LOCKFILE).write_text("4242")
    with pytest.raises(engine.EngineError, match="another kitctl run"):
        engine.apply_plan(plan)
    (repo / engine.LOCKFILE).unlink()
    (repo / "CLAUDE.md").write_text("# Edited after planning\n")
    with pytest.raises(engine.ConflictError, match="changed after the plan"):
        engine.apply_plan(plan)
    assert not (repo / ".mcp.json").exists()


def test_symlinked_target_is_a_conflict(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    target = repo / ".mcp.json"
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    assert any("symlink" in c for c in plan.conflicts)


def test_the_sandbox_confines_writes_when_set(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(engine.SANDBOX_ENV, str(elsewhere))
    with pytest.raises(engine.EngineError, match="outside"):
        engine.apply_plan(plan)
    assert not (repo / "kit.lock").exists()
    monkeypatch.delenv(engine.SANDBOX_ENV)  # unset: writes go where the person points them
    assert engine.apply_plan(plan)


def test_cli_use_status_off(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = tree(repo)
    args = ["--repo", str(repo), "--warehouse", str(WAREHOUSE)]
    assert main(["use", "brand-studio:audit", *args, "--dry-run"]) == 0
    assert "+<!-- kit:begin brand-studio -->" in capsys.readouterr().out
    assert tree(repo) == baseline
    assert main(["use", "brand-studio:audit", *args]) == 1  # no --yes
    assert tree(repo) == baseline
    assert main(["use", "brand-studio:audit", *args, "--yes"]) == 0
    capsys.readouterr()
    assert main(["status", *args, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["loadouts"] == ["brand-studio:audit"]
    assert report["slots"]["claude"]["design.generator"] == "plugin:brand-builder"
    assert "codex plugin:brand-builder" in report["pending_trust"]
    assert all(e["state"] == "ok" for e in report["entries"])
    assert main(["plan", *args]) == 0
    assert "No changes." in capsys.readouterr().out
    assert main(["off", "nope", *args, "--yes"]) == 1
    assert main(["off", "brand-studio", *args, "--yes"]) == 0
    assert tree(repo) == baseline


def test_comment_inside_a_kit_table_is_a_conflict_not_a_loss(repo: Path) -> None:
    use(repo, "brand-studio")
    config = repo / ".codex/config.toml"
    config.write_text(
        config.read_text() + "\n# my note about the next table\n[profiles.x]\na = 1\n"
    )
    before = tree(repo)
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.without_loadout(engine.read_intent(repo), "brand-studio")
    )
    assert any("a comment sits inside Kit-owned" in c for c in plan.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(plan)
    assert tree(repo) == before


def test_fallback_hook_with_unresolvable_plugin_path_is_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kitctl import harness as facts

    patched = dict(facts.CODEX.facts)
    patched["plugin_hooks"] = facts.Fact(False, "verified", "probe")
    monkeypatch.setitem(facts.HARNESS_FACTS, "codex", facts.Harness("codex", "x", "y", patched))
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    assert any(
        c.startswith("hook:brand-builder-codex-edit (codex): needs the installed plugin")
        for c in plan.conflicts
    )
    assert ".codex/hooks.json" not in plan.changes


def test_json_array_entries_are_owned_by_value() -> None:
    from kitctl import formats

    doc = {"hooks": {"PostToolUse": [{"matcher": "user"}]}}
    created = formats.json_set(doc, "/hooks/PostToolUse/-", {"matcher": "kit"})
    assert created == []
    assert doc["hooks"]["PostToolUse"] == [{"matcher": "user"}, {"matcher": "kit"}]
    formats.json_delete(doc, "/hooks/PostToolUse/-", {"matcher": "kit"}, created)
    assert doc == {"hooks": {"PostToolUse": [{"matcher": "user"}]}}
    fresh: dict[str, object] = {}
    created = formats.json_set(fresh, "/hooks/PostToolUse/-", {"matcher": "kit"})
    assert created == ["/hooks", "/hooks/PostToolUse"]
    formats.json_delete(fresh, "/hooks/PostToolUse/-", {"matcher": "kit"}, created)
    assert fresh == {}


def test_comment_only_edit_survives_off(repo: Path) -> None:
    use(repo, "brand-studio")
    config = repo / ".codex/config.toml"
    config.write_text("# why we pin gpt-5\n" + config.read_text())
    off(repo, "brand-studio")
    text = config.read_text()
    assert text.startswith("# why we pin gpt-5\n") and "chrome-devtools" not in text
    assert "# keep me" in text


def test_planted_symlinks_are_never_written_through(repo: Path, tmp_path: Path) -> None:
    victim = tmp_path / "outside-victim.txt"
    victim.write_text("untouched")
    try:
        (repo / "CLAUDE.md.kit-stage-4").symlink_to(victim)
    except OSError:
        pytest.skip("symlinks unavailable")
    plan = engine.make_plan(
        repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), "brand-studio")
    )
    stage_index = sorted(plan.changes).index("CLAUDE.md")
    (repo / "CLAUDE.md.kit-stage-4").rename(repo / f"CLAUDE.md.kit-stage-{stage_index}")
    with pytest.raises(engine.ConflictError, match="symlink"):
        engine.apply_plan(plan)
    assert victim.read_text() == "untouched"
    (repo / f"CLAUDE.md.kit-stage-{stage_index}").unlink()
    shutil.rmtree(repo / ".kit", ignore_errors=True)
    elsewhere = tmp_path / "elsewhere-state"
    elsewhere.mkdir()
    (repo / ".kit").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(engine.EngineError, match="symlink"):
        engine.apply_plan(plan)
    assert list(elsewhere.iterdir()) == []


def test_ledger_checks_survive_python_optimize(repo: Path) -> None:
    import subprocess
    import sys

    use(repo, "brand-studio")
    lock = repo / "kit.lock"
    lock.write_text(lock.read_text().replace('"value": true', '"value": false', 1))
    code = (
        "import sys, pathlib; from kitctl import engine\n"
        "try:\n    engine.read_ledger(pathlib.Path(sys.argv[1]))\n"
        "except engine.EngineError:\n    sys.exit(0)\nsys.exit(1)\n"
    )
    done = subprocess.run([sys.executable, "-O", "-c", code, str(repo)], check=False)
    assert done.returncode == 0
