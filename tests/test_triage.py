import tomllib
from pathlib import Path

import pytest

from kitctl import triage
from kitctl.cli import main
from kitctl.report import build
from kitctl.validate import validate


def item(**overrides: object) -> triage.Item:
    base = {
        "kind": "skill",
        "harness": "claude",
        "name": "s",
        "entries": [{"scope": "user", "repo": None, "path": "/u/s", "content_hash": "a" * 64}],
    }
    base.update(overrides)
    return triage.Item(**base)  # type: ignore[arg-type]


def project(repo: str) -> dict:
    return {"scope": "project", "repo": repo, "path": f"{repo}/s", "content_hash": "b" * 64}


@pytest.mark.parametrize(
    ("overrides", "decision"),
    [
        (
            {
                "variants": [
                    {"hash": "a", "locations": [{"path": "/x"}]},
                    {"hash": "b", "locations": [{"path": "/y"}, {"path": "/z"}]},
                ]
            },
            "merge",
        ),
        ({"repos_used": ["/r1", "/r2", "/r3"], "status": "active"}, "core"),
        ({"status": "active", "calls": 4}, "core"),
        (
            {"entries": [project("/code/app")], "status": "active", "repos_used": ["/code/app"]},
            "loadout",
        ),
        ({"status": "unused"}, "retire"),
        ({"status": "stale", "last_used": "2026-01-01"}, "retire"),
        ({"entries": [project("/code/web")], "status": "n/a"}, "loadout"),
        ({"status": "untracked"}, "keep"),
    ],
)
def test_proposal_rules(overrides: dict, decision: str) -> None:
    proposal = triage.propose(item(**overrides))
    assert proposal.decision == decision
    if decision == "merge":
        assert proposal.canonical == "/y"
    if decision == "loadout":
        assert proposal.loadout in {"app", "web"}


@pytest.fixture
def warehouse(harnesses: Path, tmp_path: Path) -> Path:
    from kitctl import fs

    root = tmp_path / "home/kits"
    root.mkdir()
    fs.allow(root)  # the CLI does this via warehouse.resolve(allow_writes=True)
    return root


def test_collect_items_rolls_plugin_items_up(warehouse: Path, harnesses: Path) -> None:
    items = triage.collect_items(build([harnesses]))
    names = {(i.kind, i.name) for i in items}
    assert ("plugin", "deploy-tools@demo-market") in names
    assert ("skill", "deploy") not in names  # provided by the plugin
    plugin = next(i for i in items if i.name == "deploy-tools@demo-market")
    assert plugin.provides >= 5
    assert not any(i.kind in {"settings", "instructions", "link"} for i in items)


def test_list_writes_nothing_and_missing_warehouse(harnesses: Path, tmp_path: Path, capsys) -> None:
    assert main(["triage", "--root", str(harnesses)]) == 2  # no ~/kits yet
    assert "create or clone it first" in capsys.readouterr().err
    (tmp_path / "home/kits").mkdir()
    assert main(["triage", "--list", "--root", str(harnesses)]) == 0
    assert not (tmp_path / "home/kits/triage.toml").exists()
    assert "writer" in capsys.readouterr().out


def test_accept_proposals_writes_valid_triage(warehouse: Path, harnesses: Path) -> None:
    assert main(["triage", "--accept-proposals", "--root", str(harnesses)]) == 0
    data = tomllib.loads((warehouse / "triage.toml").read_text())
    assert data["schema_version"] == 1 and data["decision"]
    assert all(d["source"] == "proposal" for d in data["decision"])
    assert validate(warehouse)["findings"] == []


def test_interactive_override_quit_and_resume(warehouse: Path, harnesses: Path) -> None:
    items = triage.collect_items(build([harnesses]))
    answers = iter(["", "r", "q"])
    decisions: dict = {}
    count = triage.interactive(
        items, decisions, warehouse, ask=lambda _: next(answers), out=lambda _: None
    )
    assert count == 2
    saved = triage.load(warehouse)
    first, second = items[0], items[1]
    assert saved[first.key]["decision"] == triage.propose(first).decision
    assert saved[second.key]["decision"] == "retire" and saved[second.key]["source"] == "user"
    seen: list[str] = []
    triage.interactive(items, saved, warehouse, ask=lambda _: "q", out=seen.append)
    assert seen[0].startswith(f"{len(items) - 2} items to triage")


def test_interactive_loadout_name_and_merge_pick(warehouse: Path) -> None:
    variants = [
        {"hash": "a" * 64, "locations": [{"path": "/x"}]},
        {"hash": "b" * 64, "locations": [{"path": "/y"}]},
    ]
    items = [item(name="one"), item(name="two", variants=variants)]
    answers = iter(["l", "My Loadout!", "m", "2"])
    decisions: dict = {}
    triage.interactive(items, decisions, warehouse, ask=lambda _: next(answers), out=lambda _: None)
    assert decisions[("skill", "claude", "one")]["loadout"] == "my-loadout"
    assert decisions[("skill", "claude", "two")]["canonical"] == "/y"
