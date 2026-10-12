import json
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.inventory import inventory
from tests.conftest import CANARY, find, validate


def test_full_fixture_scopes_and_no_values(harnesses: Path) -> None:
    report = inventory([harnesses])
    validate(report)
    assert CANARY not in json.dumps(report)
    scopes = {(e["harness"], e["scope"]) for e in report["entries"]}
    assert {
        ("claude", "managed"),
        ("claude", "user"),
        ("claude", "plugin"),
        ("claude", "project"),
        ("claude", "local"),
        ("codex", "managed"),
        ("codex", "user"),
        ("codex", "project"),
        ("shared", "project"),
    } <= scopes


def test_claude_mcp_scopes_and_safe_metadata(harnesses: Path) -> None:
    report = inventory([harnesses])
    user = find(report, kind="mcp_server", harness="claude", scope="user", name="github")[0]
    assert user["metadata"] == {
        "transport": "stdio",
        "command": "github-mcp",
        "arg_count": 2,
        "env_names": ["GITHUB_TOKEN"],
        "command_on_path": False,
    }
    sentry = find(report, kind="mcp_server", name="sentry")[0]
    assert sentry["metadata"]["url_host"] == "mcp.sentry.dev"
    assert sentry["metadata"]["header_names"] == ["Authorization"]
    local = find(report, kind="mcp_server", scope="local", name="local-db")[0]
    assert Path(local["repo"]) == harnesses.resolve()
    project = {
        e["name"] for e in find(report, kind="mcp_server", scope="project", harness="claude")
    }
    assert project == {"github", "docs"}


def test_plugin_components_are_attributed(harnesses: Path) -> None:
    report = inventory([harnesses])
    plugin = find(report, kind="plugin", name="deploy-tools@demo-market")[0]
    assert plugin["metadata"]["version"] == "1.2.0"
    provided = {(e["kind"], e["name"]) for e in find(report, provided_by=plugin["name"])}
    assert provided == {
        ("skill", "deploy"),
        ("agent", "ops"),
        ("command", "status"),
        ("hook", "PostToolUse:Write|Edit:format.sh"),
        ("mcp_server", "deploy-api"),
        ("mcp_server", "deploy-status"),
    }
    assert find(report, kind="marketplace", name="demo-market")[0]["metadata"]["repo"] == (
        "example/demo-market"
    )
    codes = {f["code"] for f in report["findings"]}
    assert "plugin-not-cached" in codes


def test_synced_skills_and_user_components(harnesses: Path) -> None:
    report = inventory([harnesses])
    morning = find(report, kind="skill", name="morning")[0]
    assert morning["provided_by"] == "claude.ai:synced"
    assert find(report, kind="agent", name="reviewer")[0]["metadata"]["description"]
    assert find(report, kind="rules", harness="claude", name="style")
    assert find(report, kind="hook", name="notify.sh")[0]["metadata"] == {"source": "script"}


def test_hooks_metadata_and_npx_warning(harnesses: Path) -> None:
    report = inventory([harnesses])
    start = find(report, kind="hook", name="SessionStart:*:session-check")[0]
    assert set(start["metadata"]) == {
        "event",
        "matcher",
        "type",
        "command",
        "command_sha256",
        "via_npx_kitctl",
        "source",
    }
    assert any(f["code"] == "hook-npx-latency" for f in report["findings"])


def test_skill_hash_equal_across_harnesses_despite_crlf(harnesses: Path) -> None:
    report = inventory([harnesses])
    hashes = {e["content_hash"] for e in find(report, kind="skill", name="writer")}
    assert len(find(report, kind="skill", name="writer")) == 3 and len(hashes) == 1


def test_codex_entries_and_trust(harnesses: Path) -> None:
    report = inventory([harnesses])
    docs = find(report, kind="mcp_server", harness="codex", name="docs")[0]
    assert docs["metadata"]["enabled_tools"] == ["search", "fetch"]
    remote = find(report, kind="mcp_server", harness="codex", name="remote")[0]
    assert remote["metadata"]["env_names"] == ["REMOTE_TOKEN"]
    assert find(report, kind="instructions", harness="shared", name="AGENTS.md")
    assert find(report, kind="instructions", name="AGENTS.override.md")
    assert find(report, kind="instructions", name="packages/api/AGENTS.md")
    assert find(report, kind="rules", harness="codex")
    assert not any(f["code"] == "codex-untrusted-project" for f in report["findings"])


def test_untrusted_codex_project(harnesses: Path, tmp_path: Path) -> None:
    config = tmp_path / "home/.codex/config.toml"
    config.write_text(config.read_text().replace('trust_level = "trusted"', 'trust_level = "x"'))
    report = inventory([harnesses])
    assert any(f["code"] == "codex-untrusted-project" for f in report["findings"])


def test_nested_instructions_and_skills(harnesses: Path) -> None:
    report = inventory([harnesses])
    assert find(report, kind="instructions", name="packages/api/CLAUDE.md")
    nested = find(report, kind="skill", name="api-helper")[0]
    assert nested["metadata"]["nested"] == "packages/api"


def test_default_claude_json_location(sandbox: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    (tmp_path / "home/.claude.json").write_text('{"mcpServers": {"x": {"command": "x"}}}')
    report = inventory([sandbox])
    assert find(report, kind="mcp_server", name="x", scope="user")


@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        (".mcp.json", "{not json", "invalid-json"),
        (".codex/config.toml", "= broken", "invalid-toml"),
    ],
)
def test_invalid_config_is_error_finding(
    sandbox: Path, relative: str, content: str, code: str
) -> None:
    path = sandbox / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    report = inventory([sandbox])
    assert [f["code"] for f in report["findings"] if f["severity"] == "error"] == [code]
    assert main(["inventory"]) == 1


def test_case_collision_reported(sandbox: Path) -> None:
    for name in ("Deploy", "deploy"):
        skill = sandbox / ".claude/skills" / name
        try:
            skill.mkdir(parents=True)
        except FileExistsError:
            pytest.skip("Case-insensitive filesystem cannot hold both names")
        (skill / "SKILL.md").write_text(name)
    report = inventory([sandbox])
    assert any(f["code"] == "case-collision" for f in report["findings"])
