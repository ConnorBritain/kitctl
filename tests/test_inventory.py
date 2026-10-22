import json
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.inventory import inventory


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_inventory_reads_hashes_without_writes_or_secret_values(sandbox: Path) -> None:
    skill = sandbox / ".agents/skills/writing"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: writing\n---\nWrite clearly.")
    config = sandbox / ".mcp.json"
    config.write_text('{"token": "fixture-secret-value"}')
    before = snapshot(sandbox)
    report = inventory([sandbox, sandbox])
    assert len(report["entries"]) == 2
    assert report["warnings"] == []
    assert "fixture-secret-value" not in json.dumps(report)
    assert snapshot(sandbox) == before


def test_asset_edits_change_skill_hash(sandbox: Path) -> None:
    skill = sandbox / ".claude/skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Demo")
    asset = skill / "helper.py"
    asset.write_text("print(1)")
    first = inventory([sandbox])
    asset.write_text("print(2)")
    assert first != inventory([sandbox])


def test_symlinks_do_not_read_outside_root(sandbox: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Private")
    skills = sandbox / ".agents/skills"
    skills.mkdir(parents=True)
    try:
        (skills / "linked").symlink_to(outside, target_is_directory=True)
        (sandbox / ".mcp.json").symlink_to(outside / "SKILL.md")
    except OSError:
        pytest.skip("Symlink creation unavailable on this platform")
    report = inventory([sandbox])
    assert report["entries"] == []
    assert len(report["warnings"]) == 2


def test_cli_explicit_scope_and_missing_root(sandbox: Path, capsys: pytest.CaptureFixture) -> None:
    (sandbox / "AGENTS.md").write_text("Instructions")
    assert main(["inventory", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["entries"]) == 1
    assert main(["inventory", "--root", str(sandbox / "missing")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == "kitctl 0.1.1\n"
