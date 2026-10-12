import json
from pathlib import Path

from kitctl import conformance
from kitctl.inventory import inventory

# Output shapes recorded from Claude Code 2.1.86 / Codex 0.153.4 (values replaced).
CODEX_MCP = json.dumps(
    [
        {
            "name": "docs",
            "enabled": True,
            "disabled_reason": None,
            "transport": {"type": "stdio"},
            "startup_timeout_sec": None,
            "tool_timeout_sec": None,
            "auth_status": "unsupported",
        },
        {"name": "remote", "enabled": True, "transport": {"type": "streamable_http"}},
        {"name": "code-review", "enabled": True, "transport": {"type": "stdio"}},
        {"name": "cua_repl", "enabled": True, "transport": {"type": "stdio"}},
    ]
)
CODEX_PLUGINS = json.dumps(
    [
        {"id": "code-review@openai-bundled", "enabled": True},
        {"id": "prose-review@agent-primitives", "enabled": False},
        {"id": "gmail@openai-curated-remote", "enabled": True},
    ]
)
CLAUDE_PLUGINS = json.dumps([{"id": "deploy-tools@demo-market", "scope": "user", "enabled": True}])
CLAUDE_MCP = """Checking MCP server health...

github: /usr/local/bin/github-mcp  - ✓ Connected
sentry: https://mcp.sentry.dev/mcp (HTTP) - ✗ Failed to connect
docs: https://docs.example.com/mcp (HTTP) - ✓ Connected
local-db: npx db-mcp - ✓ Connected
"""


def fake_runner(outputs: dict[str, str]) -> conformance.Runner:
    def run(command: list[str], cwd: Path) -> tuple[int, str]:
        key = " ".join(command)
        return (0, outputs[key]) if key in outputs else (127, "")

    return run


def test_conformance_reports_both_directions(harnesses: Path) -> None:
    report = inventory([harnesses])
    runner = fake_runner(
        {
            "claude plugin list --json": CLAUDE_PLUGINS,
            "codex mcp list --json": CODEX_MCP,
            "codex plugin list --json": CODEX_PLUGINS,
            "claude mcp list": CLAUDE_MCP,
        }
    )
    result = conformance.run(report, harnesses, health_checks=True, runner=runner)
    rows = {(m["check"], m["item"], m["missing_from"]) for m in result["mismatches"]}
    assert ("codex mcp", "cua_repl", "kitctl") in rows  # not in our fixture
    notes = {(m["check"], m["item"]): m["note"] for m in result["mismatches"]}
    # prose-review is cached and listed; a cached plugin Codex doesn't list is stale.
    assert ("codex plugins", "prose-review@agent-primitives") not in notes
    assert ("codex mcp", "code-review", "kitctl") not in rows  # found via plugin cache
    assert ("codex plugins", "gmail@openai-curated-remote", "kitctl") in rows
    assert not any(check == "claude plugins" for check, _, _ in rows)
    assert ("claude mcp", "project-db", "native") not in rows  # codex server, not compared
    plugin_gap = [m for m in result["mismatches"] if m["item"] == "deploy-api"]
    assert plugin_gap and plugin_gap[0]["note"] == conformance.GAP_PLUGIN_MCP
    assert result["unexplained"] == len(result["mismatches"]) - len(
        [m for m in result["mismatches"] if m["note"] == conformance.GAP_PLUGIN_MCP]
    )


def test_health_checks_are_opt_in_and_missing_cli_skips(harnesses: Path) -> None:
    report = inventory([harnesses])
    result = conformance.run(report, harnesses, runner=fake_runner({}))
    statuses = {c["check"]: c["status"] for c in result["checks"]}
    assert statuses["claude plugins"].startswith("skipped")
    assert statuses["claude mcp"].startswith("skipped: starts MCP servers")
    assert result["mismatches"] == []


def test_parsers_tolerate_shapes() -> None:
    assert conformance.parse_plugin_list('{"plugins": [{"name": "a", "marketplace": "m"}]}') == {
        "a@m": None
    }
    assert conformance.parse_plugin_list("not json") == {}
    assert conformance.parse_claude_mcp_list(CLAUDE_MCP) == {"github", "sentry", "docs", "local-db"}


def test_stale_codex_cache_and_plugin_mcp_are_explained(harnesses: Path) -> None:
    report = inventory([harnesses])
    runner = fake_runner(
        {
            "codex mcp list --json": json.dumps(
                [{"name": "docs"}, {"name": "remote"}, {"name": "project-db"}]
            ),
            "codex plugin list --json": json.dumps([{"id": "code-review@openai-bundled"}]),
        }
    )
    result = conformance.run(report, harnesses, runner=runner)
    notes = {(m["check"], m["item"]): m["note"] for m in result["mismatches"]}
    assert notes[("codex plugins", "prose-review@agent-primitives")] == conformance.GAP_STALE_CACHE
    assert notes[("codex mcp", "code-review")] == conformance.GAP_CODEX_PLUGIN_MCP
    assert result["unexplained"] == 0
