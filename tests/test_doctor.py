import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.doctor import doctor, run_check
from kitctl.inventory import inventory

FIXTURES = Path(__file__).parent / "fixtures"
WAREHOUSE = FIXTURES / "warehouse"
OLD_SHA = "0" * 40
# Placeholder "secrets" are assembled at runtime so no literal looks like a credential.
FAKE = "fixture" + "-placeholder"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def world(sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A sandbox HOME with user-scope state for both harnesses plus one project."""
    home = Path(os.environ["HOME"]).resolve()
    repo = sandbox.resolve()
    write(
        home / ".claude/settings.json",
        json.dumps(
            {
                "enabledPlugins": {
                    "frontend-design@claude-plugins-official": True,
                    "unknown-thing@elsewhere": True,
                },
                "extraKnownMarketplaces": {
                    "corp": {
                        "source": {
                            "source": "git",
                            "url": f"https://u:{FAKE}@git.example/m.git?x=1",
                        }
                    }
                },
            }
        ),
    )
    write(
        home / ".claude.json",
        json.dumps(
            {
                "mcpServers": {
                    "user-srv": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "some-mcp@1.0.0", "--api-key", "sk-proj-" + "x" * 30],
                        "env": {"TOKEN": FAKE + "-env"},
                    }
                },
                "projects": {
                    str(repo): {
                        "mcpServers": {
                            "local-srv": {
                                "command": "uvx",
                                "args": ["--token=" + FAKE],
                                "env": {"KEY": "${KEY}"},
                            }
                        }
                    }
                },
            }
        ),
    )
    write(
        home / ".claude/plugins/installed_plugins.json",
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "brand-builder@brand-builder": [
                        {
                            "scope": "project",
                            "projectPath": str(repo),
                            "version": "0.3.0",
                            "gitCommitSha": OLD_SHA,
                        }
                    ]
                },
            }
        ),
    )
    write(home / ".claude/skills/ui-ux-pro-max/SKILL.md", "---\nname: ui-ux-pro-max\n---\n")
    write(
        home / ".codex/config.toml",
        '# user config\n[mcp_servers.docs]\ncommand = "docs-mcp"\nenv_vars = ["DOCS_TOKEN"]\n'
        'enabled_tools = ["search"]\n\n[plugins."impeccable@impeccable"]\nenabled = true\n\n'
        '[hooks.state."plugin:x"]\ntrusted_hash = "TRUST-STORE-VALUE"\n\n'
        '[projects."/somewhere"]\ntrust_level = "trusted"\n\n'
        '[[skills.config]]\nname = "old"\nenabled = false\n',
    )
    write(
        repo / ".claude/settings.local.json",
        json.dumps({"enabledPlugins": {"hallmark@hallmark": True}}),
    )
    write(
        repo / ".codex/hooks.json",
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "apply_patch",
                            "hooks": [
                                {"type": "command", "command": "node scan.mjs --secret=" + FAKE}
                            ],
                        }
                    ]
                }
            }
        ),
    )
    write(
        repo / "CLAUDE.md",
        "# Repo\n<!-- kit:begin brand-studio -->\na\n<!-- kit:end brand-studio -->\n"
        "<!-- kit:begin brand-studio -->\nb\n<!-- kit:end brand-studio -->\n",
    )
    write(repo / "AGENTS.md", "<!-- kit:begin other -->\nunterminated\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("npx", "node"):
        script = bin_dir / name
        script.write_text("#!/bin/sh\necho ok\n")
        script.chmod(0o755)
        (bin_dir / f"{name}.cmd").write_text("@echo ok\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    beats = tmp_path / "tmp"
    beats.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(beats))
    return {"home": home, "repo": repo, "beats": beats}


def fake_runner(command: list[str]) -> tuple[int, str]:
    outputs = {
        "node": (0, "v22.1.0"),
        "npx": (0, "10.9.4"),
        "vale": (0, "vale version 2.29.0"),
    }
    if "playwright" in command:
        return 1, "npm error npx canceled due to missing packages and no YES option"
    return outputs.get(command[0], (127, ""))


def test_inventory_reads_native_state_without_secrets(world: dict[str, Path]) -> None:
    report = inventory([world["home"], world["repo"]])
    native = report["native"]
    text = json.dumps(report)
    for secret in (
        FAKE,
        "TRUST-STORE-VALUE",
        "@git.example",
        "x=1",
        "sk-proj-",
    ):
        assert secret not in text
    assert "trust_level" not in text and "/somewhere" not in text
    plugins = {(p["harness"], p["scope"], p["id"]): p["enabled"] for p in native["plugins"]}
    assert plugins[("claude", "user", "frontend-design@claude-plugins-official")] is True
    assert plugins[("claude", "local", "hallmark@hallmark")] is True
    assert plugins[("codex", "user", "impeccable@impeccable")] is True
    servers = {(s["harness"], s["scope"], s["id"]): s for s in native["mcp_servers"]}
    user = servers[("claude", "user", "user-srv")]
    assert user["args"] == ["-y", "some-mcp@1.0.0", "--api-key", "<redacted>"]
    assert user["env"] == {"TOKEN": "literal"}
    local = servers[("claude", "local", "local-srv")]
    assert local["project"] == str(world["repo"])
    assert local["args"] == ["--token=<redacted>"] and local["env"] == {"KEY": "reference"}
    docs = servers[("codex", "user", "docs")]
    assert docs["env_vars"] == ["DOCS_TOKEN"] and docs["enabled_tools"] == ["search"]
    assert native["marketplaces"][0]["source"]["location"] == "https://git.example/m.git"
    assert [h["command"] for h in native["hooks"]] == ["node scan.mjs --secret=<redacted>"]
    assert native["installed_plugins"][0]["sha"] == OLD_SHA
    assert {(s["harness"], s["skill"]) for s in native["skill_switches"]} == {("codex", "old")}
    blocks = {(Path(b["file"]).name, b["name"]): b["status"] for b in native["managed_blocks"]}
    assert blocks == {
        ("CLAUDE.md", "brand-studio"): "duplicate",
        ("AGENTS.md", "other"): "malformed",
    }
    assert report["schema_version"] == 2


def test_malformed_native_config_is_a_warning(sandbox: Path) -> None:
    write(sandbox / ".mcp.json", "{not json")
    write(sandbox / ".codex/config.toml", "[broken")
    report = inventory([sandbox])
    assert len(report["warnings"]) == 2
    assert all("Cannot parse" in w for w in report["warnings"])
    assert main(["inventory"]) == 1


def test_doctor_brand_studio_findings(world: dict[str, Path]) -> None:
    (world["beats"] / "brand-builder-hook.log").write_text(
        f"2026-10-19T10:00:00.000Z\tapply_patch\t{world['repo']}\t2\n"
        "2026-10-19T09:00:00.000Z\tEdit\t/elsewhere\t1\n"
    )
    before = snapshot(world["home"], world["repo"])
    report = doctor(WAREHOUSE, [world["home"], world["repo"]], "brand-studio", runner=fake_runner)
    assert snapshot(world["home"], world["repo"]) == before
    found = {(f["severity"], f["check"], f["subject"]) for f in report["findings"]}
    assert ("info", "unmanaged-provider", "claude plugin:anthropic/frontend-design") in found
    assert ("info", "unmanaged-provider", "claude skill:ui-ux-pro-max") in found
    assert ("info", "unmanaged-provider", "codex plugin:impeccable") in found
    assert ("error", "disable-ineffective", "claude plugin:hallmark") in found
    assert ("warning", "pin-drift", "plugin:brand-builder") in found
    assert ("warning", "stale-disable", "claude") in found  # e.g. taste-skill: home inspected
    assert ("warning", "pending-trust", "codex plugin:brand-builder") in found
    assert ("info", "mcp-launch", "claude mcp chrome-devtools") in found
    assert ("error", "managed-block", f"{world['repo'] / 'CLAUDE.md'}#brand-studio") in found
    beats = [f for f in report["findings"] if f["check"] == "hook-heartbeat"]
    assert (
        len(beats) == 1
        and "fired at 2026-10-19T10:00:00.000Z after apply_patch" in beats[0]["message"]
    )
    tools = {t["identity"]: t["status"] for t in report["tools"]}
    assert tools == {
        "tool:node": "ok",
        "tool:npx": "ok",
        "tool:playwright": "failed",
        "tool:vale": "mismatch",
    }
    assert ("error", "tool", "tool:playwright") in found
    assert ("warning", "tool", "tool:vale") in found
    assert "unknown-thing@elsewhere" in " ".join(report["harnesses"]["claude"]["unmanaged_unknown"])


def test_doctor_without_home_does_not_call_disables_stale(world: dict[str, Path]) -> None:
    report = doctor(WAREHOUSE, [world["repo"]], "brand-studio", ("claude",), runner=fake_runner)
    checks = {f["check"] for f in report["findings"]}
    assert "stale-disable" not in checks and "disable-unobserved" in checks
    beats = [f for f in report["findings"] if f["check"] == "hook-heartbeat"]
    assert beats[0]["severity"] == "warning" and "never fired" in beats[0]["message"]


def test_doctor_missing_mcp_binary(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    report = doctor(
        WAREHOUSE, [world["repo"]], "brand-studio:audit", ("codex",), runner=fake_runner
    )
    launch = [f for f in report["findings"] if f["check"] == "mcp-launch"]
    assert [(f["severity"], f["subject"]) for f in launch] == [
        ("error", "codex mcp chrome-devtools")
    ]
    assert launch[0]["hint"] == "Ships with Node.js (npm)."


def test_doctor_unmanaged_slot_conflict(world: dict[str, Path], tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    shutil.copytree(FIXTURES / "cases", cases)
    write(
        cases / "loadouts/one-gen.yaml",
        "schema_version: 2\nname: one-gen\nplugins: [plugin:gen-a]\n"
        "slots: {design.generator: plugin:gen-a}\n",
    )
    write(
        world["home"] / ".claude/settings.json",
        json.dumps({"enabledPlugins": {"gen-b@market": True}}),
    )
    report = doctor(
        cases, [world["home"], world["repo"]], "one-gen", ("claude",), runner=fake_runner
    )
    conflicts = [f for f in report["findings"] if f["check"] == "slot-conflict"]
    assert len(conflicts) == 1
    assert "plugin:gen-b (from observed (unmanaged))" in conflicts[0]["message"]


def test_doctor_cli_exit_codes(world: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    write(world["repo"] / "CLAUDE.md", "# clean\n")
    write(world["repo"] / "AGENTS.md", "# clean\n")
    assert main(["doctor", "--root", str(world["repo"])]) == 0
    assert "no files changed" in capsys.readouterr().out
    code = main(
        [
            "doctor",
            "--loadout",
            "brand-studio",
            "--warehouse",
            str(WAREHOUSE),
            "--root",
            str(world["repo"]),
            "--harness",
            "claude",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 1  # playwright and vale are absent from the sandbox PATH
    assert report["loadout"] == "brand-studio"


def test_run_check_uses_no_shell_and_reports_missing() -> None:
    code, line = run_check([sys.executable, "--version"])
    assert code == 0 and line.startswith("Python 3.")
    assert run_check(["kitctl-definitely-missing-binary"]) == (127, "")
