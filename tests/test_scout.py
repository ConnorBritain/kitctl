import json
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.model import content_hash, load_yaml
from kitctl.scout import ScoutError, scout, vendor


def details(report: dict, key: str) -> list[str]:
    return [f"{i['detail']}@{i['evidence']['file']}:{i['evidence']['line']}" for i in report[key]]


def test_scout_good_repo_facts(scout_repos: dict[str, Path], tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    report = scout(str(scout_repos["good-skill"]), cache)
    assert report["license"] == "MIT"
    assert report["licenses"][0]["evidence"]["file"] == "LICENSE"
    assert report["last_commit"].startswith("2026-10-01T12:00:00")
    assert len(report["sha"]) == 40
    assert report["id"] == f"local/example/good-skill@{report['sha']}"
    clone = Path(report["quarantine"])
    assert clone == cache / "quarantine" / "local" / "example" / f"good-skill@{report['sha']}"
    kinds = {(m["kind"], m["file"]) for m in report["manifests"]}
    assert kinds == {
        ("skill", "skills/tidy-copy/SKILL.md"),
        ("claude-plugin", ".claude-plugin/plugin.json"),
        ("agent", "agents/copy-reviewer.md"),
    }
    skill = next(m for m in report["manifests"] if m["kind"] == "skill")
    assert skill["name"] == "tidy-copy"
    for key in (
        "install_scripts",
        "telemetry",
        "outbound_network",
        "self_fetching",
        "aggressive_triggers",
    ):
        assert report[key] == [], key
    assert (clone.parent / f"{clone.name}.facts.json").is_file()


def test_scout_flags_telemetry_installers_and_network(
    scout_repos: dict[str, Path], tmp_path: Path
) -> None:
    report = scout(str(scout_repos["telemetry-mcp"]), tmp_path / "cache")
    assert report["license"] == "Apache-2.0"
    assert {item["source"] for item in report["licenses"]} == {"license-file", "package.json"}
    installs = details(report, "install_scripts")
    assert "npm postinstall: node src/setup.js@package.json:7" in installs
    assert any(i.startswith("pipes a download into a shell@README.md:") for i in installs)
    telemetry = details(report, "telemetry")
    assert any("usage statistics@src/index.js:1" in i.lower() for i in telemetry)
    assert any(i.startswith("posthog@src/index.js:") for i in [t.lower() for t in telemetry])
    assert details(report, "outbound_network") == ["calls a remote URL@src/index.js:5"]
    assert ("mcp", ".mcp.json") in {(m["kind"], m["file"]) for m in report["manifests"]}


def test_scout_flags_unlicensed_self_fetching_and_aggressive(
    scout_repos: dict[str, Path], tmp_path: Path
) -> None:
    report = scout(str(scout_repos["unlicensed-skill"]), tmp_path / "cache")
    assert report["license"] == "NOASSERTION"
    triggers = {i["detail"].lower() for i in report["aggressive_triggers"]}
    assert triggers == {"mandatory", "always use", "execute first"}
    assert all(
        i["evidence"]["file"] == "skills/always-on/SKILL.md" for i in report["aggressive_triggers"]
    )
    assert details(report, "self_fetching")[0].endswith("skills/always-on/SKILL.md:7")
    assert ("hooks", "hooks/hooks.json") in {(m["kind"], m["file"]) for m in report["manifests"]}


def test_scout_never_executes_or_writes_outside_cache(
    scout_repos: dict[str, Path], tmp_path: Path
) -> None:
    repo = scout_repos["telemetry-mcp"]
    marker = tmp_path / "executed"
    (repo / ".git" / "hooks" / "post-checkout").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    (repo / ".git" / "hooks" / "post-checkout").chmod(0o755)
    before = sorted(p for p in tmp_path.rglob("*") if "cache" not in p.parts)
    scout(str(repo), tmp_path / "cache")
    assert not marker.exists()
    after = sorted(p for p in tmp_path.rglob("*") if "cache" not in p.parts)
    assert after == before


def test_vendor_copies_pinned_path_with_provenance_and_notice(
    scout_repos: dict[str, Path], tmp_path: Path
) -> None:
    cache, warehouse = tmp_path / "cache", tmp_path / "warehouse"
    report = scout(str(scout_repos["good-skill"]), cache)
    result = vendor(report["id"], "skills/tidy-copy", warehouse, cache, pin=report["sha"][:12])
    assert result["primitive"] == "skill:tidy-copy"
    meta = load_yaml(warehouse / "inbox/skills/tidy-copy.yaml")
    assert meta["source"]["sha"] == report["sha"]
    assert meta["source"]["license"] == "MIT"
    assert meta["source"]["path"] == "skills/tidy-copy"
    assert isinstance(meta["source"]["retrieved"], str)
    assets = warehouse / "inbox/skills/tidy-copy"
    assert (assets / "SKILL.md").is_file()
    notices = (assets / "THIRD_PARTY_NOTICES.md").read_text()
    assert "Permission is hereby granted" in notices and report["sha"] in notices
    assert meta["hash"] == content_hash(meta, assets)
    assert not (warehouse / "primitives").exists()  # nothing leaves inbox without triage
    with pytest.raises(ScoutError, match="already exists"):
        vendor(report["id"], "skills/tidy-copy", warehouse, cache)


def test_vendor_refusals(scout_repos: dict[str, Path], tmp_path: Path) -> None:
    cache, warehouse = tmp_path / "cache", tmp_path / "warehouse"
    unlicensed = scout(str(scout_repos["unlicensed-skill"]), cache)
    with pytest.raises(ScoutError, match="no clear license"):
        vendor(unlicensed["id"], "skills/always-on", warehouse, cache)
    good = scout(str(scout_repos["good-skill"]), cache)
    with pytest.raises(ScoutError, match="does not match"):
        vendor(good["id"], "skills/tidy-copy", warehouse, cache, pin="deadbeef")
    with pytest.raises(ScoutError, match="escapes"):
        vendor(good["id"], "../../outside", warehouse, cache)
    with pytest.raises(ScoutError, match="not in quarantine"):
        vendor("local/example/nothing@" + "0" * 40, "x", warehouse, cache)
    telemetry = scout(str(scout_repos["telemetry-mcp"]), cache)
    warehouse.mkdir(exist_ok=True)
    (warehouse / "policy.yaml").write_text("licenses:\n  allow: [MIT]\n")
    with pytest.raises(ScoutError, match="not allowed by policy"):
        vendor(telemetry["id"], "src", warehouse, cache, kind="mcp", name="telemetry")
    assert not (warehouse / "inbox").exists()


def test_cli_scout_and_vendor(
    scout_repos: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache, warehouse = tmp_path / "cache", tmp_path / "warehouse"
    sources = [str(scout_repos[n]) for n in ("good-skill", "unlicensed-skill")]
    assert main(["scout", *sources, "--cache", str(cache), "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert [r["license"] for r in reports] == ["MIT", "NOASSERTION"]
    assert main(["scout", str(scout_repos["unlicensed-skill"]), "--cache", str(cache)]) == 0
    assert "aggressive_triggers: MANDATORY" in capsys.readouterr().out
    assert main(["scout", str(tmp_path / "missing"), "--cache", str(cache)]) == 1
    assert (
        main(
            [
                "vendor",
                reports[0]["id"],
                "--path",
                "skills/tidy-copy",
                "--warehouse",
                str(warehouse),
                "--cache",
                str(cache),
            ]
        )
        == 0
    )
    assert "stays in inbox/" in capsys.readouterr().out
    assert (
        main(
            [
                "vendor",
                reports[1]["id"],
                "--path",
                "skills/always-on",
                "--warehouse",
                str(warehouse),
                "--cache",
                str(cache),
            ]
        )
        == 1
    )
