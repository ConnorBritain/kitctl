from pathlib import Path

from kitctl import plan
from kitctl.cli import main
from kitctl.inventory import inventory


def decision(
    kind: str, harness: str, name: str, choice: str, entries: list[dict], **extra: str
) -> dict:
    return {
        "kind": kind,
        "harness": harness,
        "name": name,
        "decision": choice,
        "reason": "test",
        "locations": [f"{e['path']}#{(e['content_hash'] or '')[:12]}" for e in entries],
        **extra,
    }


def find(entries: list[dict], **criteria: str) -> list[dict]:
    return [e for e in entries if all(e.get(k) == v for k, v in criteria.items())]


def test_plan_commands_per_decision(harnesses: Path) -> None:
    entries = inventory([harnesses])["entries"]
    github = find(entries, kind="mcp_server", harness="claude", name="github")
    docs = find(entries, kind="mcp_server", harness="codex", name="docs")
    plugin = find(entries, kind="plugin", name="deploy-tools@demo-market")
    writer = [e for e in find(entries, kind="skill", name="writer") if e["harness"] == "claude"]
    reviewer = find(entries, kind="agent", name="reviewer")
    decisions = {
        ("mcp_server", "claude", "github"): decision(
            "mcp_server", "claude", "github", "retire", github
        ),
        ("mcp_server", "codex", "docs"): decision("mcp_server", "codex", "docs", "retire", docs),
        ("plugin", "claude", "deploy-tools@demo-market"): decision(
            "plugin", "claude", "deploy-tools@demo-market", "retire", plugin
        ),
        ("skill", "claude", "writer"): decision("skill", "claude", "writer", "core", writer),
        ("agent", "claude", "reviewer"): decision(
            "agent", "claude", "reviewer", "loadout", reviewer, loadout="review"
        ),
    }
    text = plan.build(decisions, entries, windows=False)
    assert "`claude mcp remove github -s user`" in text
    assert "`claude mcp remove github -s project`" in text
    assert "`codex mcp remove docs`" in text
    assert "`claude plugin disable deploy-tools@demo-market`" in text
    assert "also removes skill `deploy`" in text
    assert "Remove duplicate (identical)" in text and "--to primitives" in text
    assert 'name = "review"' in text and 'agents = ["reviewer"]' in text
    assert 'skills = ["writer"]' in text  # core loadout snippet
    assert "Nothing has been applied" in text


def test_plan_retire_file_merge_changed_and_windows(harnesses: Path) -> None:
    entries = inventory([harnesses])["entries"]
    ship = find(entries, kind="command", name="ship")
    stale = [dict(ship[0], content_hash="0" * 64)]  # recorded hash differs from today's
    decisions = {
        ("command", "claude", "ship"): decision("command", "claude", "ship", "retire", stale)
    }
    text = plan.build(decisions, entries, windows=False)
    assert "--to attic" in text and "rm " in text
    assert "Changed since triage" in text
    win = plan.build(decisions, entries, windows=True)
    assert "Remove-Item -Recurse -Force" in win


def test_plan_requires_triage(harnesses: Path, tmp_path: Path, capsys) -> None:
    (tmp_path / "home/kits").mkdir()
    assert main(["plan", "--root", str(harnesses)]) == 2
    assert "run `kitctl triage` first" in capsys.readouterr().err
    assert main(["triage", "--accept-proposals", "--root", str(harnesses)]) == 0
    capsys.readouterr()
    assert main(["plan", "--root", str(harnesses)]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path.name.startswith("plan-") and "kitctl cleanup plan" in path.read_text()


def test_merge_keeps_identical_copies_and_synced_skills(harnesses: Path) -> None:
    entries = inventory([harnesses])["entries"]
    github = find(entries, kind="mcp_server", harness="claude", name="github")
    project = next(e for e in github if e["scope"] == "project")
    decisions = {
        ("mcp_server", "claude", "github"): decision(
            "mcp_server", "claude", "github", "merge", github, canonical=project["path"]
        ),
        ("skill", "claude", "morning"): decision(
            "skill", "claude", "morning", "retire", find(entries, kind="skill", name="morning")
        ),
    }
    text = plan.build(decisions, entries, windows=False)
    assert "`claude mcp remove github -s user`" in text
    assert "-s project" not in text  # the canonical variant's per-repo copies stay
    assert "turn it off there" in text and "rm -r" not in text


def test_mcp_drift_is_per_harness(harnesses: Path) -> None:
    from kitctl import analysis

    entries = inventory([harnesses])["entries"]
    groups = {(d["kind"], d["name"], d["harness"]) for d in analysis.drift(entries)}
    assert ("mcp_server", "docs", "claude") not in groups  # three identical project copies
    assert not any(d["cross_harness"] for d in analysis.drift(entries) if d["kind"] == "mcp_server")
