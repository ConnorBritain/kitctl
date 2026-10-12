"""`kitctl conformance`: compare kitctl's inventory with the harnesses' own listings.

Unlike every other command, this one runs harness CLIs, which may update their own caches
or logs. ``claude mcp list`` starts and health-checks MCP servers, so it only runs with
``--health-checks``. kitctl never changes agent configuration.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

Runner = Callable[[list[str], Path], tuple[int, str]]

# Known listing gaps: differences that are expected, not kitctl bugs.
GAP_PLUGIN_MCP = "claude mcp list omits some plugin-provided servers"
GAP_NO_SCOPE = "native listing has no scope field; matched by name only"
GAP_CODEX_PLUGIN_MCP = "codex mcp list omits plugin-provided servers"
GAP_STALE_CACHE = "cached but not installed per `codex plugin list`; cleanup candidate"
EXPLAINED = frozenset({GAP_PLUGIN_MCP, GAP_CODEX_PLUGIN_MCP, GAP_STALE_CACHE})


def subprocess_runner(command: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str]:
    executable = shutil.which(command[0])
    if not executable:
        return 127, ""
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return result.returncode, result.stdout


def _items(document: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(document, Mapping):
        for key in keys:
            if isinstance(document.get(key), list):
                document = document[key]
                break
    return (
        [item for item in document if isinstance(item, Mapping)]
        if isinstance(document, list)
        else []
    )


def _plugin_id(item: Mapping[str, Any]) -> str | None:
    for key in ("id", "pluginId", "plugin_id"):
        if isinstance(item.get(key), str):
            return str(item[key])
    name = item.get("name")
    marketplace = item.get("marketplace") or item.get("marketplaceName")
    if isinstance(name, str) and "@" in name:
        return name
    if isinstance(name, str) and isinstance(marketplace, str):
        return f"{name}@{marketplace}"
    return name if isinstance(name, str) else None


def parse_plugin_list(stdout: str) -> dict[str, bool | None]:
    """``{plugin id: enabled}`` from ``claude/codex plugin list --json`` (tolerant)."""
    try:
        document = json.loads(stdout)
    except ValueError:
        return {}
    found: dict[str, bool | None] = {}
    for item in _items(document, "plugins", "installed", "items"):
        pid = _plugin_id(item)
        if pid:
            enabled = item.get("enabled")
            found[pid] = enabled if isinstance(enabled, bool) else None
    return found


def parse_codex_mcp_list(stdout: str) -> dict[str, bool | None]:
    try:
        document = json.loads(stdout)
    except ValueError:
        return {}
    found: dict[str, bool | None] = {}
    for item in _items(document, "servers", "mcp_servers"):
        if isinstance(item.get("name"), str):
            enabled = item.get("enabled")
            found[item["name"]] = enabled if isinstance(enabled, bool) else None
    return found


_CLAUDE_MCP_LINE = re.compile(r"^([^\s:][^:]*?):\s+\S")


def parse_claude_mcp_list(stdout: str) -> set[str]:
    """Server names from ``claude mcp list`` text output (``name: target - status``)."""
    names = set()
    for line in stdout.splitlines():
        if line.startswith(("Checking", "No MCP")) or not line.strip():
            continue
        match = _CLAUDE_MCP_LINE.match(line)
        if match:
            names.add(match.group(1).strip())
    return names


def _compare(
    check: str,
    native: Iterable[str],
    ours: Iterable[str],
    note: str = "",
    explained_ours: Iterable[str] = (),
    explanation: str = GAP_PLUGIN_MCP,
) -> list[dict[str, str]]:
    """Differences both ways; ours-only names in ``explained_ours`` get ``explanation``."""
    native_set, ours_set, explained = set(native), set(ours), set(explained_ours)
    rows = []
    for name in sorted(native_set - ours_set):
        rows.append({"check": check, "item": name, "missing_from": "kitctl", "note": note})
    for name in sorted(ours_set - native_set):
        rows.append(
            {
                "check": check,
                "item": name,
                "missing_from": "native",
                "note": explanation if name in explained else note,
            }
        )
    return rows


def run(
    report: dict[str, Any],
    cwd: Path,
    health_checks: bool = False,
    runner: Runner = subprocess_runner,
) -> dict[str, Any]:
    entries = report["entries"]
    results: dict[str, Any] = {"checks": [], "mismatches": []}

    def record(check: str, command: list[str], status: str, count: int | None = None) -> None:
        results["checks"].append(
            {"check": check, "command": " ".join(command), "status": status, "native_count": count}
        )

    claude_plugins = {
        e["name"]
        for e in entries
        if e["kind"] == "plugin"
        and e["harness"] == "claude"
        and e["metadata"].get("origin") not in {"synced", "skills-dir"}
    }
    command = ["claude", "plugin", "list", "--json"]
    code, out = runner(command, cwd)
    if code == 127:
        record("claude plugins", command, "skipped: claude not installed")
    else:
        native = parse_plugin_list(out)
        record("claude plugins", command, "ok" if code == 0 else f"exit {code}", len(native))
        results["mismatches"] += _compare("claude plugins", native, claude_plugins)

    codex_mcp = {
        e["name"] for e in entries if e["kind"] == "mcp_server" and e["harness"] == "codex"
    }
    command = ["codex", "mcp", "list", "--json"]
    code, out = runner(command, cwd)
    if code == 127:
        record("codex mcp", command, "skipped: codex not installed")
    else:
        native = parse_codex_mcp_list(out)
        record("codex mcp", command, "ok" if code == 0 else f"exit {code}", len(native))
        codex_plugin_mcp = {
            e["name"]
            for e in entries
            if e["kind"] == "mcp_server" and e["harness"] == "codex" and e["scope"] == "plugin"
        }
        results["mismatches"] += _compare(
            "codex mcp",
            native,
            codex_mcp,
            GAP_NO_SCOPE,
            explained_ours=codex_plugin_mcp,
            explanation=GAP_CODEX_PLUGIN_MCP,
        )
        codex_plugins = {
            e["name"] for e in entries if e["kind"] == "plugin" and e["harness"] == "codex"
        }
        command = ["codex", "plugin", "list", "--json"]
        code, out = runner(command, cwd)
        native_plugins = parse_plugin_list(out)
        record("codex plugins", command, "ok" if code == 0 else f"exit {code}", len(native_plugins))
        # Every cached plugin we list but Codex doesn't is a stale cache entry.
        results["mismatches"] += _compare(
            "codex plugins",
            native_plugins,
            codex_plugins,
            GAP_NO_SCOPE,
            explained_ours=codex_plugins - set(native_plugins),
            explanation=GAP_STALE_CACHE,
        )

    if health_checks:
        repo = Path(cwd).resolve()
        visible: set[str] = set()
        plugin_servers: set[str] = set()
        for e in entries:
            if e["kind"] != "mcp_server" or e["harness"] != "claude":
                continue
            if e["scope"] == "plugin":
                plugin_servers.add(e["name"])
            elif e["scope"] in {"user", "managed"}:
                visible.add(e["name"])
            elif e["repo"] and Path(e["repo"]).resolve() == repo:
                visible.add(e["name"])  # local or project scope for this repository
        command = ["claude", "mcp", "list"]
        code, out = runner(command, cwd)
        native_names = parse_claude_mcp_list(out)
        status = "ok" if code == 0 else f"exit {code}"
        record("claude mcp (health checks)", command, status, len(native_names))
        # Plugin servers are ours too; when the native list omits them it's a known gap.
        results["mismatches"] += _compare(
            "claude mcp", native_names, visible | plugin_servers, explained_ours=plugin_servers
        )
    else:
        record(
            "claude mcp",
            ["claude", "mcp", "list"],
            "skipped: starts MCP servers; pass --health-checks to run it",
        )
    unexplained = [m for m in results["mismatches"] if m["note"] not in EXPLAINED]
    results["unexplained"] = len(unexplained)
    return results
