from datetime import UTC, datetime
from typing import Any

from kitctl import analysis


def entry(
    kind: str, name: str, scope: str = "user", digest: str | None = "a" * 64, **extra: Any
) -> dict:
    base = {
        "kind": kind,
        "harness": "claude",
        "scope": scope,
        "name": name,
        "path": f"/{scope}/{name}/{extra.get('repo') or ''}{digest and digest[:3]}",
        "content_hash": digest,
        "repo": None,
        "provided_by": None,
        "metadata": {},
    }
    base.update(extra)
    return base


def test_drift_and_duplicates() -> None:
    entries = [
        entry("skill", "writer", digest="a" * 64),
        entry("skill", "writer", "project", digest="b" * 64, repo="/r"),
        entry("skill", "copy", "project", digest="a" * 64, repo="/r"),
    ]
    drift = analysis.drift(entries)
    assert drift[0]["name"] == "writer" and len(drift[0]["variants"]) == 2
    assert drift[0]["cross_harness"] is False
    hooks = [
        entry("hook", "Pre:Bash:x", digest="1" * 64),
        entry("hook", "Pre:Bash:x", digest="2" * 64),
    ]
    assert analysis.drift(hooks) == []
    crossed = [
        entry("skill", "w", digest="a" * 64),
        entry("skill", "w", digest="b" * 64, harness="codex"),
    ]
    assert analysis.drift(crossed)[0]["cross_harness"] is True
    dupes = analysis.duplicates(entries)
    assert dupes[0]["names"] == ["copy", "writer"]


def test_shadowing_winner_per_precedence() -> None:
    entries = [
        entry("skill", "writer", "user"),
        entry("skill", "writer", "project", repo="/r"),
        entry("mcp_server", "github", "user"),
        entry("mcp_server", "github", "local", repo="/r"),
        entry("mcp_server", "github", "project", repo="/r"),
    ]
    found = {(s["kind"], s["winner_scope"]) for s in analysis.shadowing(entries)}
    assert found == {("skill", "user"), ("mcp_server", "local")}


def test_overlap_ignores_launchers() -> None:
    entries = [
        entry("mcp_server", "gh1", metadata={"command": "github-mcp"}),
        entry("mcp_server", "gh2", "project", metadata={"command": "github-mcp"}, repo="/r"),
        entry("mcp_server", "a", metadata={"command": "npx"}),
        entry("mcp_server", "b", metadata={"command": "npx"}),
        entry("mcp_server", "s1", metadata={"url_host": "mcp.example"}),
        entry("mcp_server", "s2", metadata={"url_host": "mcp.example"}),
    ]
    keys = {o["key"] for o in analysis.overlap(entries)}
    assert keys == {"command:github-mcp", "url:mcp.example"}


def test_health_findings() -> None:
    entries = [
        entry("skill", "bare", metadata={"frontmatter_name": None}),
        entry("mcp_server", "x", metadata={"command": "x", "command_on_path": False}),
        entry(
            "mcp_server",
            "off",
            metadata={"command": "y", "command_on_path": False, "disabled": True},
        ),
        entry(
            "settings",
            "settings.json",
            digest="c" * 64,
            metadata={"enabled_plugins": {"ghost@m": True, "here@m": False, "s@synced": True}},
        ),
        entry("plugin", "here@m"),
    ]
    codes = sorted(f.code for f in analysis.health(entries))
    assert codes == [
        "mcp-command-missing",
        "plugin-enabled-not-installed",
        "plugin-installed-disabled",
        "skill-missing-frontmatter",
    ]


def test_usage_status_and_matching() -> None:
    now = datetime(2026, 10, 12, tzinfo=UTC)
    entries = [
        entry("skill", "writer"),
        entry("skill", "deploy", "plugin", provided_by="deploy-tools@m"),
        entry("skill", "old"),
        entry("skill", "never"),
        entry("settings", "s.json"),
        entry("plugin", "deploy-tools@m"),
    ]
    records = [
        {
            "harness": "claude",
            "kind": "skill",
            "name": "writer",
            "repo": None,
            "calls": 3,
            "last_used": "2026-10-10T00:00:00Z",
        },
        {
            "harness": "claude",
            "kind": "skill",
            "name": "deploy-tools:deploy",
            "repo": None,
            "calls": 1,
            "last_used": "2026-10-01T00:00:00Z",
        },
        {
            "harness": "claude",
            "kind": "command",
            "name": "old",
            "repo": None,
            "calls": 1,
            "last_used": "2026-01-01T00:00:00Z",
        },
    ]
    statuses = [u["status"] for u in analysis.usage_by_entry(entries, records, 60, now)]
    assert statuses == ["active", "active", "stale", "unused", "n/a", "active"]
