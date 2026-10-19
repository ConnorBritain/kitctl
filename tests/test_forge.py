"""Forge end to end over three local fixture repos; nothing leaves the sandbox."""

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from kitctl.cli import main
from kitctl.forge import ForgeError, load_candidates, new_brief, propose, verify
from kitctl.model import load_yaml
from kitctl.resolve import resolve
from kitctl.scout import scout, vendor


def write_yaml(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def snapshot(root: Path, skip: tuple[str, ...]) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.relative_to(root).as_posix().startswith(skip)
    }


@pytest.fixture
def forged(scout_repos: dict[str, Path], tmp_path: Path) -> dict[str, Any]:
    warehouse, cache = tmp_path / "warehouse", tmp_path / "cache"
    warehouse.mkdir()
    new_brief(
        warehouse,
        "copy-kit",
        "Check marketing copy without adding a voice.",
        required_slots=["copy.checker", "copy.metrics", "copy.rules"],
    )
    reports = {name: scout(str(path), cache) for name, path in scout_repos.items()}
    good, telemetry, unlicensed = (
        reports[n] for n in ("good-skill", "telemetry-mcp", "unlicensed-skill")
    )
    vendor(good["id"], "skills/tidy-copy", warehouse, cache, pin=good["sha"])
    candidates = f"""schema_version: 1
candidates:
  - id: {good["id"]}
    source: {{ url: {good["source"]}, sha: {good["sha"]} }}
    facts: {{ license: MIT }}
    fills: [copy.checker]
    opinion_bias: none
    verdict: vendor
    config: {{ kind: skill, name: tidy-copy, path: skills/tidy-copy }}
    evidence:
      fills: [skills/tidy-copy/SKILL.md:3]
      opinion_bias: [skills/tidy-copy/SKILL.md:8]
      verdict: [fact:license, LICENSE:1]
      config: [skills/tidy-copy/SKILL.md:2]
  - id: {telemetry["id"]}
    fills: [copy.metrics]
    opinion_bias: none
    verdict: adopt
    config:
      kind: mcp
      name: telemetry-mcp
      command: npx
      pin: {{ package: telemetry-mcp, version: 0.4.0 }}
      args: [--no-usage-statistics]
    evidence:
      fills: [README.md:1]
      opinion_bias: [src/index.js:4]
      verdict: [fact:license]
      config: [package.json:3, src/index.js:1]
  - id: {unlicensed["id"]}
    fills: [copy.rules]
    opinion_bias: high
    verdict: vendor
    config: {{ kind: skill, name: always-on, path: skills/always-on }}
    evidence:
      fills: [skills/always-on/SKILL.md:3]
      opinion_bias: [skills/always-on/SKILL.md:7]
      verdict: [fact:aggressive_triggers]
      config: [skills/always-on/SKILL.md:2]
"""
    write_yaml(warehouse / "forge/copy-kit/candidates.yaml", candidates)
    return {"warehouse": warehouse, "cache": cache, "reports": reports, "text": candidates}


def test_forge_end_to_end_excludes_bad_candidates(
    forged: dict[str, Any], scout_repos: dict[str, Path], tmp_path: Path
) -> None:
    warehouse, cache = forged["warehouse"], forged["cache"]
    outside_before = snapshot(tmp_path, ("warehouse/", "cache/"))
    result = propose(warehouse, "copy-kit", cache)
    assert snapshot(tmp_path, ("warehouse/", "cache/")) == outside_before
    assert result["loadout"] == {
        "schema_version": 2,
        "name": "copy-kit",
        "description": "Check marketing copy without adding a voice.",
        "skills": ["skill:tidy-copy"],
        "slots": {"copy.checker": "skill:tidy-copy"},
    }
    telemetry_id = forged["reports"]["telemetry-mcp"]["id"]
    unlicensed_id = forged["reports"]["unlicensed-skill"]["id"]
    assert any(r.startswith("telemetry (") for r in result["excluded"][telemetry_id])
    assert any(r.startswith("outbound network") for r in result["excluded"][telemetry_id])
    assert "no clear license (NOASSERTION)" in result["excluded"][unlicensed_id]
    assert any(r.startswith("self fetching") for r in result["excluded"][unlicensed_id])
    assert result["unfilled"] == ["copy.metrics", "copy.rules"]
    gaps = (warehouse / "forge/copy-kit/gaps.md").read_text()
    assert "`copy.metrics`" in gaps and unlicensed_id in gaps
    assert not (warehouse / "inbox/mcp/telemetry-mcp.yaml").exists()
    assert not (warehouse / "inbox/skills/always-on").exists()
    assert not (warehouse / "loadouts").exists() and not (warehouse / "primitives").exists()
    # Triage simulation: the draft is a valid loadout once moved out of inbox/.
    shutil.copytree(warehouse / "inbox/skills", warehouse / "primitives/skills")
    shutil.copytree(warehouse / "inbox/loadouts", warehouse / "loadouts")
    plan = resolve(warehouse, "copy-kit", "claude")
    assert plan["slots"]["copy.checker"]["provider"] == "skill:tidy-copy"
    assert any(e["target"] == ".claude/skills/tidy-copy/" for e in plan["effects"])


def test_uncited_and_invented_claims_are_rejected(forged: dict[str, Any]) -> None:
    warehouse, cache = forged["warehouse"], forged["cache"]
    path = warehouse / "forge/copy-kit/candidates.yaml"
    path.write_text(forged["text"].replace("      verdict: [fact:license, LICENSE:1]\n", "", 1))
    with pytest.raises(ForgeError, match="evidence.verdict: uncited claim"):
        load_candidates(warehouse, "copy-kit", cache)
    path.write_text(
        forged["text"].replace("facts: { license: MIT }", "facts: { license: GPL-3.0 }")
    )
    with pytest.raises(ForgeError, match="facts come only from scout"):
        load_candidates(warehouse, "copy-kit", cache)
    path.write_text(
        forged["text"].replace("skills/tidy-copy/SKILL.md:8", "skills/tidy-copy/SKILL.md:99")
    )
    with pytest.raises(ForgeError, match="past the end of the file"):
        load_candidates(warehouse, "copy-kit", cache)
    path.write_text(forged["text"].replace("[README.md:1]", "[a hunch]"))
    with pytest.raises(ForgeError, match="not file:line"):
        load_candidates(warehouse, "copy-kit", cache)


def test_two_providers_for_a_required_slot_is_an_error(forged: dict[str, Any]) -> None:
    warehouse, cache = forged["warehouse"], forged["cache"]
    text = forged["text"].replace("fills: [copy.metrics]", "fills: [copy.checker]")
    text = text.replace("constraints", "constraints")
    brief = warehouse / "forge/copy-kit/brief.yaml"
    brief.write_text(
        brief.read_text()
        .replace("no_telemetry: true", "no_telemetry: false")
        .replace("no_runtime_network: true", "no_runtime_network: false")
    )
    (warehouse / "forge/copy-kit/candidates.yaml").write_text(text)
    with pytest.raises(ForgeError, match="slot copy.checker has 2 providers"):
        propose(warehouse, "copy-kit", cache)


def test_verify_runs_declared_evals_without_a_shell(forged: dict[str, Any]) -> None:
    warehouse, cache = forged["warehouse"], forged["cache"]
    good = forged["reports"]["good-skill"]["id"]
    brief = warehouse / "forge/copy-kit/brief.yaml"
    data = load_yaml(brief)
    data["evals"] = [
        {
            "name": "skill text present",
            "run": [
                sys.executable,
                "-c",
                "import pathlib; print(pathlib.Path('SKILL.md').read_text())",
            ],
            "cwd": "inbox:skills/tidy-copy",
            "expect": "filler words",
        },
        {
            "name": "fixture glob",
            "run": [sys.executable, "-c", "import sys; print(sys.argv[1:])", "skills/*/SKILL.md"],
            "cwd": f"quarantine:{good}",
            "expect": "skills/tidy-copy/SKILL.md",
        },
    ]
    from kitctl.forge import _dump

    brief.write_text(_dump(data))
    record = verify(warehouse, "copy-kit", cache)
    statuses = {r["name"]: r["status"] for r in record["results"]}
    assert statuses["skill text present"] == "passed"
    assert statuses["fixture glob"] == "passed"
    assert statuses["claude plugin validate --strict inbox (skills)"] in {"passed", "skipped"}
    assert record["ok"] is True
    data["evals"].append({"name": "fails", "run": [sys.executable, "-c", "raise SystemExit(3)"]})
    brief.write_text(_dump(data))
    assert verify(warehouse, "copy-kit", cache)["ok"] is False
    assert (warehouse / "forge/copy-kit/verify.json").is_file()


def test_cli_forge(forged: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    common = ["--warehouse", str(forged["warehouse"]), "--cache", str(forged["cache"])]
    assert main(["forge", "new", "second", "--purpose", "x", "--slot", "a.b", *common]) == 0
    assert main(["forge", "new", "second", "--purpose", "x", *common]) == 1
    assert main(["forge", "propose", "copy-kit", *common]) == 0
    out = capsys.readouterr().out
    assert "wrote inbox/loadouts/copy-kit.yaml" in out and "unfilled slot copy.rules" in out
    assert main(["forge", "propose", "second", *common]) == 1
