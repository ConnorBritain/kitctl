"""Worker agents from the warehouse: each harness's ready file copied whole, updated, removed."""

import shutil
from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main
from kitctl.inventory import inventory
from kitctl.model import ModelError, parse_loadout, parse_primitive
from kitctl.resolve import resolve
from kitctl.starter import init_warehouse
from kitctl.validate import validate

CASES = Path(__file__).parent / "fixtures" / "cases"
CLAUDE = (
    b"---\nname: reviewer\ndescription: Reviews the diff it is handed.\n"
    b"tools: Read, Grep\n---\nReview the diff you are given; report findings only.\n"
)
CODEX = (
    b'name = "reviewer"\ndescription = "Reviews the diff it is handed."\n'
    b'developer_instructions = """\nReview the diff you are given; report findings only.\n"""\n'
)


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def agent_warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "kits"
    agents = root / "primitives" / "agents"
    write(
        agents / "reviewer.yaml",
        b"schema_version: 2\nkind: agent\nname: reviewer\n"
        b"description: Reviews the diff it is handed.\nharnesses: [claude, codex]\n",
    )
    write(agents / "reviewer" / "claude.md", CLAUDE)
    write(agents / "reviewer" / "codex.toml", CODEX)
    write(
        root / "loadouts" / "review.yaml",
        b"schema_version: 2\nname: review\ndescription: Reviews.\nextends: []\n"
        b"agents: [agent:reviewer]\nprofiles:\n  quiet:\n    agents: []\n",
    )
    return root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path))
    repo = tmp_path / "project"
    repo.mkdir()
    write(repo / "CLAUDE.md", b"# Project\n")
    return repo.resolve()


def files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".kit" not in p.relative_to(root).parts
    }


def plan(repo: Path, warehouse: Path, on: bool = True, target: str = "review") -> engine.Plan:
    intent = engine.read_intent(repo)
    change = engine.with_loadout(intent, target) if on else engine.without_loadout(intent, target)
    return engine.make_plan(repo, warehouse, change)


def test_apply_copies_each_harness_file_byte_exact_and_off_removes_it(
    project: Path, agent_warehouse: Path
) -> None:
    baseline = files(project)
    p = plan(project, agent_warehouse)
    assert p.conflicts == []
    engine.apply_plan(p)
    assert (project / ".claude/agents/reviewer.md").read_bytes() == CLAUDE
    assert (project / ".codex/agents/reviewer.toml").read_bytes() == CODEX
    entries = engine.status(project, agent_warehouse)["entries"]
    assert entries and all(e["state"] == "ok" for e in entries)
    engine.apply_plan(plan(project, agent_warehouse, on=False))
    assert files(project) == baseline
    assert not (project / ".claude").exists() and not (project / ".codex").exists()


def test_a_crlf_agent_file_is_copied_as_lf(project: Path, agent_warehouse: Path) -> None:
    write(agent_warehouse / "primitives/agents/reviewer/claude.md", CLAUDE.replace(b"\n", b"\r\n"))
    engine.apply_plan(plan(project, agent_warehouse))
    assert (project / ".claude/agents/reviewer.md").read_bytes() == CLAUDE
    assert all(e["state"] == "ok" for e in engine.status(project, agent_warehouse)["entries"])


def test_a_changed_agent_in_the_warehouse_updates_the_copy(
    project: Path, agent_warehouse: Path
) -> None:
    engine.apply_plan(plan(project, agent_warehouse))
    write(
        agent_warehouse / "primitives/agents/reviewer/codex.toml", CODEX.replace(b"only", b"ONLY")
    )
    p = plan(project, agent_warehouse)
    assert p.conflicts == []
    engine.apply_plan(p)
    assert b"ONLY" in (project / ".codex/agents/reviewer.toml").read_bytes()
    assert (project / ".claude/agents/reviewer.md").read_bytes() == CLAUDE


def test_a_hand_edited_copy_is_drift_not_overwritten(project: Path, agent_warehouse: Path) -> None:
    engine.apply_plan(plan(project, agent_warehouse))
    target = project / ".claude/agents/reviewer.md"
    write(target, CLAUDE + b"My own line.\n")
    states = {e["id"]: e["state"] for e in engine.status(project, agent_warehouse)["entries"]}
    assert [s for i, s in states.items() if ".claude/agents/reviewer.md" in i] == ["drift"]
    off = plan(project, agent_warehouse, on=False)
    assert any("drift" in c and ".claude/agents/reviewer.md" in c for c in off.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(off)
    assert target.read_bytes() == CLAUDE + b"My own line.\n"


def test_an_existing_unowned_agent_file_is_a_conflict(project: Path, agent_warehouse: Path) -> None:
    write(project / ".codex/agents/reviewer.toml", b'name = "mine"\n')
    p = plan(project, agent_warehouse)
    assert any(".codex/agents/reviewer.toml" in c and "unmanaged" in c for c in p.conflicts)
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(p)
    assert (project / ".codex/agents/reviewer.toml").read_bytes() == b'name = "mine"\n'


def test_a_profile_without_agents_writes_none(project: Path, agent_warehouse: Path) -> None:
    assert resolve(agent_warehouse, "review:quiet", "claude")["effects"] == []
    p = plan(project, agent_warehouse, target="review:quiet")
    assert not any("agents/" in target for target in p.changes)


def test_user_scope_writes_under_home(agent_warehouse: Path) -> None:
    home = Path.home().resolve()
    baseline = files(home)
    code = main(["use", "review", "--user", "--yes", "--warehouse", str(agent_warehouse)])
    assert code == 0
    assert (home / ".claude/agents/reviewer.md").read_bytes() == CLAUDE
    assert (home / ".codex/agents/reviewer.toml").read_bytes() == CODEX
    assert main(["off", "review", "--user", "--yes", "--warehouse", str(agent_warehouse)]) == 0
    assert files(home) == baseline


def test_codex_project_agents_are_unverified_and_cursor_is_a_gap(agent_warehouse: Path) -> None:
    write(
        agent_warehouse / "primitives/agents/reviewer.yaml",
        b"schema_version: 2\nkind: agent\nname: reviewer\n"
        b"description: Reviews the diff it is handed.\nharnesses: [claude, codex, cursor]\n",
    )
    claude = resolve(agent_warehouse, "review", "claude")
    assert [(e["target"], e["format"], e["key"]) for e in claude["effects"]] == [
        (".claude/agents/reviewer.md", "asset", "claude.md")
    ]
    assert not any(g["capability"].startswith("agent") for g in claude["gaps"])
    codex = resolve(agent_warehouse, "review", "codex")
    assert [e["target"] for e in codex["effects"]] == [".codex/agents/reviewer.toml"]
    assert [(g["capability"], g["severity"]) for g in codex["gaps"]] == [
        ("agent_file", "unverified"),
        ("project_trust", "trust-required"),
    ]
    user = resolve(agent_warehouse, "review", "codex", scope="user")
    assert not any(g["capability"].startswith("agent") for g in user["gaps"])
    cursor = resolve(agent_warehouse, "review", "cursor")
    assert cursor["effects"] == []
    gap = next(g for g in cursor["gaps"] if g["capability"] == "agent_file")
    assert gap["severity"] == "unsupported" and "by hand" in gap["remedy"]


def test_a_namespaced_agent_lands_under_its_last_segment(agent_warehouse: Path) -> None:
    agents = agent_warehouse / "primitives/agents"
    write(
        agents / "acme" / "reviewer.yaml",
        b"schema_version: 2\nkind: agent\nname: acme/reviewer\ndescription: Acme's.\n"
        b"harnesses: [claude]\n",
    )
    write(agents / "acme" / "reviewer" / "claude.md", CLAUDE)
    write(
        agent_warehouse / "loadouts/acme.yaml",
        b"schema_version: 2\nname: acme\nagents: [agent:acme/reviewer]\n",
    )
    targets = [e["target"] for e in resolve(agent_warehouse, "acme", "claude")["effects"]]
    assert targets == [".claude/agents/reviewer.md"]
    assert validate(agent_warehouse)["problems"] == []


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("no-assets", "no agent directory no-assets/"),
        ("no-codex", "codex is listed but no-codex/codex.toml is missing"),
        ("bad-toml", "bad-toml/codex.toml is not valid TOML"),
        ("no-instructions", "needs a string developer_instructions"),
        ("wrong-name", "frontmatter name must be 'wrong-name'"),
    ],
)
def test_broken_agents_are_validation_problems(name: str, message: str) -> None:
    with pytest.raises(ModelError) as error:
        parse_primitive(CASES, f"agent:{name}")
    assert any(p.code == "agent" and message in p.message for p in error.value.problems)
    report = validate(CASES)
    assert any(
        p["code"] == "agent" and p["path"] == f"primitives/agents/{name}.yaml"
        for p in report["problems"]
    )


def test_a_missing_harness_file_at_apply_time_is_a_conflict(
    project: Path, agent_warehouse: Path
) -> None:
    (agent_warehouse / "primitives/agents/reviewer/codex.toml").unlink()
    with pytest.raises(engine.EngineError):
        plan(project, agent_warehouse)  # validation catches it before any plan exists


def test_loadout_agents_must_be_agent_references(agent_warehouse: Path) -> None:
    write(
        agent_warehouse / "loadouts/wrong.yaml",
        b"schema_version: 2\nname: wrong\nagents: [skill:reviewer]\n",
    )
    with pytest.raises(ModelError) as error:
        parse_loadout(agent_warehouse, "wrong")
    assert {p.code for p in error.value.problems} == {"reference"}


def test_inventory_lists_agent_files(sandbox: Path) -> None:
    write(sandbox / ".claude/agents/reviewer.md", CLAUDE)
    write(sandbox / ".codex/agents/reviewer.toml", CODEX)
    write(sandbox / ".codex/agents/broken.toml", b"name = \n")
    agents = sorted(
        (e["name"], Path(e["path"]).name)
        for e in inventory([sandbox])["entries"]
        if e["kind"] == "agent"
    )
    assert agents == [
        ("broken", "broken.toml"),
        ("reviewer", "reviewer.md"),
        ("reviewer", "reviewer.toml"),
    ]


def test_init_makes_an_agents_folder(tmp_path: Path) -> None:
    init_warehouse(tmp_path / "kits", git=False)
    assert (tmp_path / "kits/primitives/agents/.gitkeep").is_file()


def test_agents_are_not_copied_from_a_missing_directory(
    project: Path, agent_warehouse: Path
) -> None:
    """The engine refuses on its own, even if a primitive slipped past validation."""
    conflicts: list[str] = []
    effect = {
        "target": ".claude/agents/reviewer.md",
        "format": "asset",
        "key": "claude.md",
        "value": "hash",
        "source": "agent:reviewer",
    }
    shutil.rmtree(agent_warehouse / "primitives/agents/reviewer")
    assert engine._expand_directories(agent_warehouse, [effect], conflicts) == []
    assert conflicts == ["agent:reviewer: no reviewer/claude.md in the warehouse"]


def test_two_agents_with_the_same_last_segment_conflict(
    project: Path, agent_warehouse: Path
) -> None:
    agents = agent_warehouse / "primitives/agents"
    write(
        agents / "acme" / "reviewer.yaml",
        b"schema_version: 2\nkind: agent\nname: acme/reviewer\ndescription: Acme's.\n"
        b"harnesses: [claude]\n",
    )
    write(agents / "acme" / "reviewer" / "claude.md", CLAUDE.replace(b"diff", b"change"))
    write(
        agent_warehouse / "loadouts/both.yaml",
        b"schema_version: 2\nname: both\nagents: [agent:reviewer, agent:acme/reviewer]\n",
    )
    p = plan(project, agent_warehouse, target="both")
    assert any(
        ".claude/agents/reviewer.md: both" in c and "agent:acme/reviewer" in c for c in p.conflicts
    )
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(p)
    assert not (project / ".claude/agents").exists()


def test_a_folder_where_an_agent_file_goes_is_a_conflict_before_any_write(
    project: Path, agent_warehouse: Path
) -> None:
    (project / ".claude/agents/reviewer.md").mkdir(parents=True)
    baseline = files(project)
    p = plan(project, agent_warehouse)
    assert ".claude/agents/reviewer.md exists and is not a regular file" in p.conflicts
    with pytest.raises(engine.ConflictError):
        engine.apply_plan(p)
    assert files(project) == baseline and not (project / ".kit/journal.json").exists()
