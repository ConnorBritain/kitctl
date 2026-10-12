import json
import sys
from pathlib import Path

import pytest

from kitctl import __version__
from kitctl.cli import main
from kitctl.inventory import inventory
from tests.conftest import link


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def by_kind(report: dict, kind: str) -> list[dict]:
    return [entry for entry in report["entries"] if entry["kind"] == kind]


def test_inventory_reads_hashes_without_writes_or_secret_values(sandbox: Path) -> None:
    skill = sandbox / ".agents/skills/writing"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: writing\n---\nWrite clearly.")
    config = sandbox / ".mcp.json"
    config.write_text('{"token": "fixture-secret-value"}')
    before = snapshot(sandbox)
    report = inventory([sandbox, sandbox])
    assert len(report["entries"]) == 2
    assert report["findings"] == []
    assert report["hash_scheme"] == 2
    assert all(entry["hash_scheme"] == 2 for entry in report["entries"])
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


# Issue 1: 0.0.1 skipped every symlink, hiding shared tooling and exiting "partial".


def test_link_outside_allowed_roots_is_recorded_not_read(sandbox: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Private")
    skills = sandbox / ".agents/skills"
    skills.mkdir(parents=True)
    link(skills / "linked", outside)
    link(sandbox / ".mcp.json", outside / "SKILL.md")
    report = inventory([sandbox])
    links = by_kind(report, "link")
    assert {entry["name"] for entry in links} == {".agents/skills/linked", ".mcp.json"}
    assert all(entry["content_hash"] is None for entry in links)
    assert all(entry["target_in_scan"] is False for entry in links)
    assert by_kind(report, "skill") == [] and by_kind(report, "config") == []
    assert {finding["code"] for finding in report["findings"]} == {"external-link"}
    assert main(["inventory", "--json"]) == 0


def test_link_into_harness_dir_is_read_through(sandbox: Path, tmp_path: Path) -> None:
    claude = Path(tmp_path / "home/.claude")
    shared = claude / "skills/shared"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: shared\n---\n")
    (claude / "settings.json").write_text("{}")
    (sandbox / ".claude/skills").mkdir(parents=True)
    link(sandbox / ".claude/skills/shared", shared)
    link(sandbox / ".claude/settings.json", claude / "settings.json")
    report = inventory([sandbox])
    skill = by_kind(report, "skill")[0]
    assert skill["name"] == "shared" and skill["content_hash"]
    assert skill["link_target"] == str(shared.resolve())
    config = by_kind(report, "config")[0]
    assert config["name"] == ".claude/settings.json" and config["content_hash"]
    assert len(by_kind(report, "link")) == 2
    assert report["findings"] == []


def test_linked_skills_directory_and_in_root_targets(sandbox: Path) -> None:
    real = sandbox / "tooling/skills"
    (real / "alpha").mkdir(parents=True)
    (real / "alpha/SKILL.md").write_text("alpha")
    (sandbox / ".agents").mkdir()
    link(sandbox / ".agents/skills", real)
    report = inventory([sandbox])
    assert [entry["name"] for entry in by_kind(report, "skill")] == ["alpha"]
    assert by_kind(report, "link")[0]["target_in_scan"] is True


def test_broken_link_is_a_finding_not_a_failure(sandbox: Path, tmp_path: Path) -> None:
    link(sandbox / "AGENTS.md", tmp_path / "home/.claude/missing.md")
    report = inventory([sandbox])
    assert [finding["code"] for finding in report["findings"]] == ["broken-link"]
    assert report["findings"][0]["severity"] == "warning"
    assert main(["inventory"]) == 0


def test_link_cycle_inside_skill_terminates(sandbox: Path) -> None:
    skill = sandbox / ".claude/skills/loop"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("loop")
    link(skill / "self", skill)
    report = inventory([sandbox])
    assert [entry["name"] for entry in by_kind(report, "skill")] == ["loop"]


@pytest.mark.skipif(sys.platform != "win32", reason="Junctions are Windows-only")
def test_windows_junction_is_a_link(sandbox: Path, tmp_path: Path) -> None:
    import _winapi  # type: ignore[import-not-found]

    target = tmp_path / "home/.agents/skills/junctioned"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("j")
    (sandbox / ".agents/skills").mkdir(parents=True)
    _winapi.CreateJunction(str(target), str(sandbox / ".agents/skills/junctioned"))
    report = inventory([sandbox])
    assert by_kind(report, "link")[0]["name"] == ".agents/skills/junctioned"
    assert by_kind(report, "skill")[0]["content_hash"]


# Issue 2: line endings and junk files caused false drift.


def test_line_endings_and_junk_do_not_change_skill_hash(sandbox: Path) -> None:
    lf = sandbox / ".claude/skills/one"
    crlf = sandbox / ".agents/skills/one"
    for skill, newline in ((lf, b"\n"), (crlf, b"\r\n")):
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(b"---" + newline + b"name: one" + newline + b"---")
    (crlf / ".DS_Store").write_bytes(b"\0junk")
    (crlf / "__pycache__").mkdir()
    (crlf / "__pycache__/x.pyc").write_bytes(b"\0")
    hashes = {entry["content_hash"] for entry in by_kind(inventory([sandbox]), "skill")}
    assert len(hashes) == 1


def test_cli_explicit_scope_and_missing_root(sandbox: Path, capsys: pytest.CaptureFixture) -> None:
    (sandbox / "AGENTS.md").write_text("Instructions")
    assert main(["inventory", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["entries"]) == 1
    assert main(["inventory", "--root", str(sandbox / "missing")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_report_guard_redacts_secret_shaped_paths(
    sandbox: Path, capsys: pytest.CaptureFixture
) -> None:
    token = "sk-ant-" + "api03-" + "A" * 30
    skill = sandbox / ".claude/skills" / token
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    assert main(["inventory", "--json"]) == 0
    output = capsys.readouterr().out
    assert token not in output
    report = json.loads(output)
    assert report["findings"][-1]["code"] == "secret-redacted"


def test_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"kitctl {__version__}\n"
