"""Analysis over inventory entries and usage: drift, duplicates, shadowing, overlap,
health, and staleness. Pure functions; nothing here reads the filesystem."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from kitctl.model import Finding

Entry = dict[str, Any]

# Kinds compared by content across locations.
# Hooks are excluded from drift: distinct hooks often share event/matcher/program.
COMPARABLE = frozenset({"skill", "agent", "command", "rules", "mcp_server"})
DUPLICABLE = COMPARABLE | {"instructions", "hook"}
USAGE_KINDS = frozenset({"skill", "agent", "command", "mcp_server", "plugin"})
LAUNCHERS = frozenset(
    {
        "npx",
        "uvx",
        "node",
        "python",
        "python3",
        "bunx",
        "bun",
        "deno",
        "docker",
        "pnpm",
        "uv",
        "pipx",
        "sh",
        "bash",
        "cmd",
        "powershell",
        "pwsh",
    }
)

# Precedence when the same name exists at several scopes, highest first. Documented in
# docs/harness-reference.md; sources: code.claude.com skills, subagents, and MCP pages.
PRECEDENCE: dict[tuple[str, str], tuple[str, ...]] = {
    ("claude", "skill"): ("managed", "user", "project"),
    ("claude", "agent"): ("managed", "project", "user"),
    ("claude", "command"): ("managed", "user", "project"),
    ("claude", "mcp_server"): ("managed", "local", "project", "user"),
    ("codex", "mcp_server"): ("managed", "project", "user"),
}


def _location(entry: Entry) -> dict[str, Any]:
    return {
        "harness": entry["harness"],
        "scope": entry["scope"],
        "repo": entry["repo"],
        "path": entry["path"],
        "provided_by": entry["provided_by"],
    }


def drift(entries: list[Entry]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, list[Entry]]] = defaultdict(lambda: defaultdict(list))
    for entry in entries:
        if entry["kind"] in COMPARABLE and entry["content_hash"] and not entry["provided_by"]:
            groups[(entry["kind"], entry["name"])][entry["content_hash"]].append(entry)
    return [
        {
            "kind": kind,
            "name": name,
            "cross_harness": len({e["harness"] for members in variants.values() for e in members})
            > 1,
            "variants": [
                {"hash": digest, "locations": [_location(e) for e in members]}
                for digest, members in sorted(variants.items())
            ],
        }
        for (kind, name), variants in sorted(groups.items())
        if len(variants) > 1
    ]


def duplicates(entries: list[Entry]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Entry]] = defaultdict(list)
    for entry in entries:
        if entry["kind"] in DUPLICABLE and entry["content_hash"]:
            groups[(entry["kind"], entry["content_hash"])].append(entry)
    return [
        {
            "kind": kind,
            "hash": digest,
            "names": sorted({e["name"] for e in members}),
            "locations": [_location(e) for e in members],
        }
        for (kind, digest), members in sorted(groups.items())
        if len(members) > 1
    ]


def shadowing(entries: list[Entry]) -> list[dict[str, Any]]:
    """Same name at several scopes; the winner per documented precedence (per repo)."""
    groups: dict[tuple[str, str, str], list[Entry]] = defaultdict(list)
    for entry in entries:
        if (entry["harness"], entry["kind"]) in PRECEDENCE and not entry["provided_by"]:
            groups[(entry["harness"], entry["kind"], entry["name"])].append(entry)
    results = []
    for (harness, kind, name), members in sorted(groups.items()):
        order = PRECEDENCE[(harness, kind)]
        repos = sorted({e["repo"] for e in members if e["repo"]}) or [None]
        for repo in repos:
            visible = [e for e in members if e["repo"] in (None, repo) and e["scope"] in order]
            scopes = {e["scope"] for e in visible}
            if len(scopes) < 2:
                continue
            winner = min(scopes, key=order.index)
            results.append(
                {
                    "harness": harness,
                    "kind": kind,
                    "name": name,
                    "repo": repo,
                    "winner_scope": winner,
                    "locations": [_location(e) for e in visible],
                }
            )
    return results


def overlap(entries: list[Entry]) -> list[dict[str, Any]]:
    """MCP servers with the same command or URL host under different names."""
    groups: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        if entry["kind"] != "mcp_server":
            continue
        meta = entry["metadata"]
        if meta.get("url_host"):
            groups[f"url:{meta['url_host']}"].append(entry)
        elif meta.get("command") and meta["command"] not in LAUNCHERS:
            groups[f"command:{meta['command']}"].append(entry)
    return [
        {"key": key, "servers": [{**_location(e), "name": e["name"]} for e in members]}
        for key, members in sorted(groups.items())
        if len({e["name"] for e in members}) > 1
    ]


def health(entries: list[Entry]) -> list[Finding]:
    findings: list[Finding] = []
    missing: dict[tuple[str, str, str], list[Entry]] = defaultdict(list)
    for entry in entries:
        meta = entry["metadata"]
        if entry["kind"] == "skill" and not (
            meta.get("frontmatter_name") and meta.get("description")
        ):
            findings.append(
                Finding(
                    "warning",
                    "skill-missing-frontmatter",
                    entry["path"],
                    "SKILL.md lacks frontmatter name or description; harnesses may not list it",
                )
            )
        if (
            entry["kind"] == "mcp_server"
            and meta.get("command_on_path") is False
            and not meta.get("disabled")
        ):
            missing[(entry["harness"], entry["name"], str(meta.get("command")))].append(entry)
    for (harness, name, command), places in sorted(missing.items()):
        where = f" ({len(places)} configs)" if len(places) > 1 else ""
        findings.append(
            Finding(
                "warning",
                "mcp-command-missing",
                places[0]["path"],
                f"{harness} MCP server {name!r}: command {command} not found{where}",
            )
        )
    findings.extend(_plugin_health(entries))
    return findings


_SETTINGS_ORDER = {"user": 0, "project": 1, "local": 2, "managed": 3}


def _plugin_health(entries: list[Entry]) -> list[Finding]:
    installed = {e["name"] for e in entries if e["kind"] == "plugin" and e["harness"] == "claude"}
    decisions: dict[str, list[tuple[int, bool, str]]] = defaultdict(list)
    for entry in entries:
        if entry["kind"] == "settings" and entry["harness"] == "claude":
            for pid, enabled in entry["metadata"].get("enabled_plugins", {}).items():
                decisions[pid].append(
                    (_SETTINGS_ORDER.get(entry["scope"], 0), enabled, entry["path"])
                )
    findings = []
    for pid, votes in sorted(decisions.items()):
        origin = pid.partition("@")[2]
        if (
            any(enabled for _, enabled, _ in votes)
            and pid not in installed
            and origin not in {"synced", "skills-dir", "inline"}
        ):
            path = next(p for _, enabled, p in votes if enabled)
            findings.append(
                Finding(
                    "warning",
                    "plugin-enabled-not-installed",
                    path,
                    f"Plugin {pid} is enabled but not installed",
                )
            )
        if pid in installed and not any(enabled for _, enabled, _ in votes):
            findings.append(
                Finding(
                    "info",
                    "plugin-installed-disabled",
                    votes[0][2],
                    f"Plugin {pid} is installed but disabled everywhere it is configured",
                )
            )
    return findings


def _usage_names(entry: Entry) -> set[str]:
    names = {entry["name"]}
    front = entry["metadata"].get("frontmatter_name")
    if front:
        names.add(front)
    if entry["provided_by"]:
        plugin = entry["provided_by"].partition("@")[0]
        names |= {f"{plugin}:{name}" for name in list(names)}
    if entry["kind"] == "plugin":
        names = {entry["name"].partition("@")[0]}
    return names


def _record_id(record: dict[str, Any]) -> str:
    return "\x1f".join((record["harness"], record["kind"], record["name"], record["repo"] or ""))


def usage_by_entry(
    entries: list[Entry],
    records: list[dict[str, Any]],
    stale_days: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Calls, last use, and status per entry (active, stale, unused, or n/a)."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=stale_days)).isoformat()
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        index[(record["harness"], record["kind"], record["name"])].append(record)
        if record["kind"] == "command":  # skills are invocable as /name too
            index[(record["harness"], "skill", record["name"])].append(record)
        if record["kind"] in {"skill", "agent", "command"} and ":" in record["name"]:
            index[(record["harness"], "plugin", record["name"].partition(":")[0])].append(record)
    results = []
    for entry in entries:
        if entry["kind"] not in USAGE_KINDS:
            results.append(
                {"status": "n/a", "calls": 0, "last_used": "", "repos": [], "records": {}}
            )
            continue
        harness = "claude" if entry["harness"] == "shared" else entry["harness"]
        matched = [
            record
            for name in _usage_names(entry)
            for record in index.get((harness, entry["kind"], name), [])
        ]
        if entry["repo"]:
            matched = [r for r in matched if r["repo"] == entry["repo"]]
        unique = {_record_id(r): r for r in matched}
        calls = sum(r["calls"] for r in unique.values())
        last = max((r["last_used"] for r in unique.values()), default="")
        status = "unused" if not calls else ("active" if last >= cutoff else "stale")
        results.append(
            {
                "status": status,
                "calls": calls,
                "last_used": last,
                "repos": sorted({r["repo"] for r in unique.values() if r["repo"]}),
                "records": {key: r["calls"] for key, r in unique.items()},
            }
        )
    return results


def analyze(entries: list[Entry], records: list[dict[str, Any]], stale_days: int) -> dict[str, Any]:
    found_drift = drift(entries)
    found_dupes = duplicates(entries)
    found_shadow = shadowing(entries)
    found_overlap = overlap(entries)
    findings = health(entries)
    for item in found_drift:
        findings.append(
            Finding(
                "warning",
                "drift",
                item["variants"][0]["locations"][0]["path"],
                f"{item['kind']} {item['name']!r} has {len(item['variants'])} differing copies"
                + (" (Claude and Codex copies differ)" if item["cross_harness"] else ""),
            )
        )
    for item in found_overlap:
        names = sorted({s["name"] for s in item["servers"]})
        findings.append(
            Finding(
                "info",
                "mcp-overlap",
                item["servers"][0]["path"],
                f"MCP servers {', '.join(names)} share {item['key']}",
            )
        )
    per_entry = usage_by_entry(entries, records, stale_days)
    matched = {key for usage in per_entry for key in usage["records"]}
    unmatched = sorted(
        (r for r in records if _record_id(r) not in matched),
        key=lambda r: (-r["calls"], r["harness"], r["kind"], r["name"]),
    )
    return {
        "stale_days": stale_days,
        "unmatched_usage": [
            {k: r[k] for k in ("harness", "kind", "name", "repo", "calls", "last_used")}
            for r in unmatched
        ],
        "drift": found_drift,
        "duplicates": found_dupes,
        "shadowing": found_shadow,
        "overlap": found_overlap,
        "findings": [f.to_dict() for f in findings],
        "usage_by_entry": per_entry,
    }
