"""`kitctl triage`: propose core / loadout / retire / merge / keep for each primitive.

Decisions are saved to ``<warehouse>/triage.toml``. Nothing here touches agent config.
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kitctl import fs, tomlw

SCHEMA_VERSION = 1
TRIAGE_KINDS = ("plugin", "skill", "agent", "command", "mcp_server", "hook", "rules")
DECISIONS = ("core", "loadout", "retire", "merge", "keep", "skip")
CORE_REPOS = 3

Key = tuple[str, str, str]  # (kind, harness, name)


@dataclass
class Item:
    kind: str
    harness: str
    name: str
    entries: list[dict[str, Any]]
    status: str = "n/a"
    calls: int = 0
    last_used: str = ""
    repos_used: list[str] = field(default_factory=list)
    provides: int = 0
    variants: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> Key:
        return (self.kind, self.harness, self.name)

    def locations(self) -> list[str]:
        return [f"{e['path']}#{(e['content_hash'] or '')[:12]}" for e in self.entries]


@dataclass
class Proposal:
    decision: str
    reason: str
    loadout: str = ""
    canonical: str = ""


def collect_items(report: dict[str, Any]) -> list[Item]:
    """Group triageable entries; plugin-provided entries roll up into their plugin."""
    rows = {(r["kind"], r["harness"], r["name"]): r for r in report["matrix"]}
    grouped: dict[Key, list[dict[str, Any]]] = defaultdict(list)
    provides: dict[str, int] = defaultdict(int)
    for entry in report["entries"]:
        if entry["kind"] not in TRIAGE_KINDS:
            continue
        if entry["provided_by"] and "@" in entry["provided_by"]:
            provides[entry["provided_by"]] += 1
            continue
        grouped[(entry["kind"], entry["harness"], entry["name"])].append(entry)
    drift: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for group in report["analysis"]["drift"]:
        for harness in ("claude", "codex", "shared"):
            if group.get("harness") not in (None, harness):
                continue
            mine = [
                {
                    "hash": variant["hash"],
                    "locations": [loc for loc in variant["locations"] if loc["harness"] == harness],
                }
                for variant in group["variants"]
            ]
            mine = [variant for variant in mine if variant["locations"]]
            if len(mine) > 1:
                drift[(group["kind"], group["name"], harness)] = mine
    items = []
    for (kind, harness, name), entries in grouped.items():
        row = rows.get((kind, harness, name), {})
        items.append(
            Item(
                kind,
                harness,
                name,
                entries,
                status=row.get("status", "n/a"),
                calls=row.get("calls", 0),
                last_used=row.get("last_used", ""),
                repos_used=row.get("repos_used", []),
                provides=provides.get(name, 0) if kind == "plugin" else 0,
                variants=drift.get((kind, name, harness), []),
            )
        )
    order = {kind: index for index, kind in enumerate(TRIAGE_KINDS)}
    return sorted(items, key=lambda i: (order[i.kind], i.harness, i.name))


def propose(item: Item) -> Proposal:
    scopes = {e["scope"] for e in item.entries}
    repos = sorted({e["repo"] for e in item.entries if e["repo"]})
    if len(item.variants) > 1:
        canonical_variant = max(item.variants, key=lambda v: len(v["locations"]))
        canonical = canonical_variant["locations"][0]["path"]
        return Proposal(
            "merge",
            f"{len(item.variants)} differing copies; keep one (most common copy suggested)",
            canonical=canonical,
        )
    if len(item.repos_used) >= CORE_REPOS:
        return Proposal("core", f"used in {len(item.repos_used)} repos")
    if item.status == "active" and scopes & {"user", "managed"}:
        return Proposal("core", f"user-level and active ({item.calls} calls)")
    if item.status == "active" and item.repos_used:
        return Proposal(
            "loadout",
            f"active in {len(item.repos_used)} repo(s) only",
            loadout=_slug(Path(item.repos_used[0]).name),
        )
    if item.status in {"stale", "unused"}:
        when = f"last used {item.last_used[:10]}" if item.last_used else "no recorded use"
        copies = f", {len(item.entries)} copies" if len(item.entries) > 1 else ""
        return Proposal("retire", f"{item.status}: {when}{copies}")
    if repos and not scopes & {"user", "managed", "plugin"}:
        return Proposal(
            "loadout", "project-scoped only; no usage signal", loadout=_slug(Path(repos[0]).name)
        )
    return Proposal("keep", "no usage signal for this kind; decide manually")


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "-" for c in text.lower()).strip("-.")
    return cleaned[:64] or "loadout"


# -- storage -------------------------------------------------------------------


def triage_path(warehouse: Path) -> Path:
    return warehouse / "triage.toml"


def load(warehouse: Path) -> dict[Key, dict[str, Any]]:
    path = triage_path(warehouse)
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text("utf-8"))
    return {(d["kind"], d["harness"], d["name"]): d for d in data.get("decision", [])}


def save(warehouse: Path, decisions: dict[Key, dict[str, Any]]) -> None:
    order = {kind: index for index, kind in enumerate(TRIAGE_KINDS)}
    rows = sorted(
        decisions.values(), key=lambda d: (order.get(d["kind"], 99), d["harness"], d["name"])
    )
    document = {"schema_version": SCHEMA_VERSION, "decision": rows}
    header = (
        "kitctl triage decisions. Edit freely; `kitctl plan` turns these into a manual\n"
        "cleanup checklist. decision: core | loadout | retire | merge | keep | skip"
    )
    fs.write_atomic(triage_path(warehouse), tomlw.dumps(document, header=header))


def record(
    item: Item, proposal: Proposal, decision: str, source: str, **extra: str
) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "harness": item.harness,
        "name": item.name,
        "decision": decision,
        "loadout": extra.get("loadout", proposal.loadout if decision == "loadout" else ""),
        "canonical": extra.get("canonical", proposal.canonical if decision == "merge" else ""),
        "reason": proposal.reason,
        "proposed": proposal.decision,
        "source": source,
        "decided_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "locations": item.locations(),
    }


# -- interaction ------------------------------------------------------------------

KEYS = {
    "": "accept",
    "c": "core",
    "l": "loadout",
    "r": "retire",
    "m": "merge",
    "k": "keep",
    "s": "skip",
    "q": "quit",
}


def describe(item: Item, proposal: Proposal) -> str:
    lines = [f"{item.kind} · {item.harness} · {item.name}"]
    for entry in item.entries:
        where = Path(entry["repo"]).name if entry["repo"] else entry["scope"]
        digest = (entry["content_hash"] or "")[:12]
        lines.append(f"    {where:20} {entry['scope']:8} {digest:12}  {entry['path']}")
    if item.provides:
        lines.append(f"    provides {item.provides} skills/agents/commands/servers")
    usage = f"{item.calls} calls, last {item.last_used[:10]}" if item.calls else "no recorded calls"
    lines.append(f"    usage: {item.status} ({usage})")
    for index, variant in enumerate(item.variants, 1):
        paths = ", ".join(loc["path"] for loc in variant["locations"])
        lines.append(f"    variant {index} {variant['hash'][:12]}: {paths}")
    suffix = f" → {proposal.loadout}" if proposal.loadout else ""
    lines.append(f"    proposal: {proposal.decision}{suffix} ({proposal.reason})")
    return "\n".join(lines)


def interactive(
    items: list[Item],
    decisions: dict[Key, dict[str, Any]],
    warehouse: Path,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Walk undecided items; save after every decision. Returns how many were decided."""
    decided = 0
    pending = [item for item in items if item.key not in decisions]
    out(f"{len(pending)} items to triage ({len(items) - len(pending)} already decided).")
    out("[enter] accept  c core  l loadout  r retire  m merge  k keep  s skip  q quit")
    for position, item in enumerate(pending, 1):
        proposal = propose(item)
        out(f"\n[{position}/{len(pending)}] " + describe(item, proposal))
        while True:
            answer = ask("decision> ").strip().lower()
            action = KEYS.get(answer[:1] if answer else "")
            if action:
                break
            out("  keys: enter c l r m k s q")
        if action == "quit":
            break
        choice = proposal.decision if action == "accept" else action
        extra: dict[str, str] = {}
        if choice == "loadout" and action != "accept":
            extra["loadout"] = _slug(
                ask(f"loadout name [{proposal.loadout or 'core'}]> ").strip()
                or proposal.loadout
                or "core"
            )
        if choice == "merge" and action != "accept" and item.variants:
            options = [v["locations"][0]["path"] for v in item.variants]
            for index, path in enumerate(options, 1):
                out(f"    {index}. {path}")
            pick = ask("canonical variant number [1]> ").strip() or "1"
            extra["canonical"] = (
                options[int(pick) - 1]
                if pick.isdigit() and 0 < int(pick) <= len(options)
                else options[0]
            )
        decisions[item.key] = record(item, proposal, choice, "user", **extra)
        save(warehouse, decisions)
        decided += 1
    return decided
