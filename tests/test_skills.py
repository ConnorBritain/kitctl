"""Skill directories from the warehouse: copied as owned files, updated, and removed by off."""

import shutil
from pathlib import Path

import pytest

from kitctl import engine

WAREHOUSE = Path(__file__).parent / "fixtures" / "warehouse"


@pytest.fixture
def skill_warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "kits"
    (root / "primitives" / "skills" / "hello" / "references").mkdir(parents=True)
    (root / "loadouts").mkdir()
    (root / "primitives" / "skills" / "hello.yaml").write_bytes(
        b"schema_version: 2\nkind: skill\nname: hello\ndescription: Says hello.\n"
    )
    (root / "primitives" / "skills" / "hello" / "SKILL.md").write_bytes(
        b"---\nname: hello\ndescription: Says hello.\n---\nSay hello.\n"
    )
    # A CRLF asset, as a Windows checkout of the warehouse would have it.
    (root / "primitives" / "skills" / "hello" / "references" / "notes.md").write_bytes(
        b"Notes.\r\n"
    )
    (root / "loadouts" / "greet.yaml").write_text(
        "schema_version: 2\nname: greet\ndescription: Greets.\nextends: []\nskills: [skill:hello]\n"
    )
    return root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path))
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "CLAUDE.md").write_bytes(b"# Project\n")
    return repo.resolve()


def files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".kit" not in p.relative_to(root).parts
    }


def plan(repo: Path, warehouse: Path, on: bool = True) -> engine.Plan:
    intent = engine.read_intent(repo)
    change = engine.with_loadout(intent, "greet") if on else engine.without_loadout(intent, "greet")
    return engine.make_plan(repo, warehouse, change)


def test_a_skill_directory_is_copied_for_each_harness_and_off_removes_it(
    project: Path, skill_warehouse: Path
) -> None:
    baseline = files(project)
    p = plan(project, skill_warehouse)
    assert p.conflicts == []
    engine.apply_plan(p)
    for root in (".claude/skills/hello", ".agents/skills/hello"):
        assert (project / root / "SKILL.md").read_text().endswith("Say hello.\n")
        assert (project / root / "references" / "notes.md").read_bytes() == b"Notes.\n"
    assert engine.status(project, skill_warehouse)["entries"]
    assert all(e["state"] == "ok" for e in engine.status(project, skill_warehouse)["entries"])
    engine.apply_plan(plan(project, skill_warehouse, on=False))
    assert files(project) == baseline
    assert not (project / ".claude").exists() and not (project / ".agents").exists()


def test_a_changed_skill_in_the_warehouse_updates_the_copy(
    project: Path, skill_warehouse: Path
) -> None:
    engine.apply_plan(plan(project, skill_warehouse))
    (skill_warehouse / "primitives" / "skills" / "hello" / "SKILL.md").write_bytes(
        b"---\nname: hello\ndescription: Says hello.\n---\nSay hello twice.\n"
    )
    engine.apply_plan(plan(project, skill_warehouse))
    assert "twice" in (project / ".claude/skills/hello/SKILL.md").read_text()


def test_an_existing_unowned_skill_file_is_a_conflict(project: Path, skill_warehouse: Path) -> None:
    (project / ".claude/skills/hello").mkdir(parents=True)
    (project / ".claude/skills/hello/SKILL.md").write_text("mine\n")
    p = plan(project, skill_warehouse)
    assert any(".claude/skills/hello/SKILL.md" in c for c in p.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(p)
    assert (project / ".claude/skills/hello/SKILL.md").read_text() == "mine\n"


def test_a_skill_without_skill_md_or_with_binary_assets_is_a_conflict(
    project: Path, skill_warehouse: Path
) -> None:
    (skill_warehouse / "primitives" / "skills" / "hello" / "logo.png").write_bytes(b"\x89PNG\xff")
    assert any("not UTF-8" in c for c in plan(project, skill_warehouse).conflicts)
    shutil.rmtree(skill_warehouse / "primitives" / "skills" / "hello")
    assert any("no skill directory" in c for c in plan(project, skill_warehouse).conflicts)


def test_a_git_folder_inside_a_skill_is_not_copied(project: Path, skill_warehouse: Path) -> None:
    """A skill cloned in place keeps its .git; the hash ignores it, and so does the copy."""
    git = skill_warehouse / "primitives" / "skills" / "hello" / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "objects" / "ab").write_bytes(b"\x78\x9c\xff\x00")
    (git / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    p = plan(project, skill_warehouse)
    assert p.conflicts == []
    engine.apply_plan(p)
    assert (project / ".claude/skills/hello/SKILL.md").is_file()
    assert not (project / ".claude/skills/hello/.git").exists()
