"""`kitctl promote`: turn triage decisions into warehouse content.

For each ``core`` / ``loadout`` decision the current copy is adopted into ``primitives/``
(``merge`` decisions adopt the chosen canonical copy) and referenced from
``loadouts/core.toml`` or ``loadouts/<name>.toml``. Loadout files are edited in place
(comments and hand-added entries kept; nothing is removed). Repositories get a
``kit.toml`` only with ``write_kit_files``; otherwise the snippet is printed.

Writes only the warehouse (and, when asked, ``kit.toml`` files). Agent config is never
touched; ``kitctl apply`` does that after you review its diff.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kitctl import adopt, fs
from kitctl._vendor import tomlkit
from kitctl.validate import PLUGIN_ID, SLUG

LISTS = {
    "skill": "skills",
    "agent": "agents",
    "command": "commands",
    "rules": "rules",
    "mcp_server": "mcp",
    "hook": "hooks",
    "plugin": "plugins",
}
ADOPT_KIND = {"skill": "skill", "agent": "agent", "command": "command", "rules": "rules"}


@dataclass
class Result:
    adopted: list[str] = field(default_factory=list)
    added: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # loadout -> list
    kit_files: dict[str, list[str]] = field(default_factory=dict)  # repo -> loadouts
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, loadout: str, kind: str, ref: str) -> None:
        refs = self.added.setdefault(loadout, {}).setdefault(LISTS[kind], [])
        if ref not in refs:
            refs.append(ref)


def _source(decision: dict[str, Any], matching: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = decision.get("canonical")
    if canonical:
        for entry in matching:
            if entry["path"] == canonical:
                return entry
    return next((e for e in matching if e["scope"] == "user"), matching[0])


def _predicted_name(kind: str, entry: dict[str, Any]) -> str:
    if kind in ("mcp_server",):
        return adopt.slug(entry["name"])
    if kind == "hook":
        command = str(entry["metadata"].get("command") or entry["name"])
        return adopt.slug(Path(command).stem)
    path = Path(entry["path"])
    return adopt.slug(path.name if kind == "skill" else path.stem)


def _add_harness(target: Path, harness: str) -> None:
    """A copy adopted from Claude and an identical one from Codex: install for both."""
    meta = target / "primitive.toml"
    document = tomlkit.parse(meta.read_text("utf-8"))
    harnesses = document.get("harnesses")
    if harness in ("claude", "codex") and harnesses is not None and harness not in harnesses:
        harnesses.append(harness)
        fs.write_atomic(meta, tomlkit.dumps(document))


def promote(
    decisions: dict[Any, dict[str, Any]],
    entries: list[dict[str, Any]],
    warehouse: Path,
    *,
    dry_run: bool = False,
) -> Result:
    result = Result()
    if not dry_run:
        fs.allow(warehouse)
    for key in sorted(decisions):
        decision = decisions[key]
        kind, harness, name = decision["kind"], decision["harness"], decision["name"]
        verdict = decision["decision"]
        if verdict not in ("core", "loadout", "merge") or kind not in LISTS:
            continue
        label = f"{kind} {name} ({harness})"
        loadout = {"core": "core", "loadout": decision.get("loadout") or ""}.get(verdict)
        if verdict == "loadout" and not (loadout and SLUG.match(loadout)):
            result.skipped.append(f"{label}: decision has no valid loadout name")
            continue
        matching = [
            e
            for e in entries
            if e["kind"] == kind
            and e["harness"] == harness
            and e["name"] == name
            and (kind == "plugin" or not e["provided_by"])
        ]
        if not matching:
            result.skipped.append(f"{label}: not in the current inventory")
            continue
        repos = sorted({e["repo"] for e in matching if e["repo"]})
        if kind == "plugin":
            if harness != "claude" or not PLUGIN_ID.match(name):
                result.skipped.append(f"{label}: only Claude Code marketplace plugins are compiled")
                continue
            ref = name
        else:
            source = _source(decision, matching)
            ref = _predicted_name(kind, source)
            if dry_run:
                result.adopted.append(f"would adopt {source['path']} -> primitives ({kind} {ref})")
            else:
                try:
                    outcome = _adopt(kind, source, entries, warehouse)
                except (adopt.AdoptError, OSError, ValueError) as error:
                    result.skipped.append(f"{label}: {error}")
                    continue
                target = Path(outcome["target"])
                ref = target.name
                if outcome["status"] == "already adopted":
                    _add_harness(target, harness)
                result.adopted.append(f"{outcome['status']}: {source['path']} -> {target}")
        if loadout is None:
            result.notes.append(f"{label}: merged copy adopted as {ref}; add it to a loadout")
            continue
        result.add(loadout, kind, ref)
        if loadout != "core":
            for repo in repos:
                wanted = result.kit_files.setdefault(repo, [])
                if loadout not in wanted:
                    wanted.append(loadout)
    return result


def _adopt(
    kind: str, source: dict[str, Any], entries: list[dict[str, Any]], warehouse: Path
) -> dict[str, Any]:
    scope = {"harness": source["harness"], "scope": source["scope"], "repo": source["repo"]}
    if kind == "mcp_server":
        return adopt.adopt_mcp(source["name"], entries, warehouse, "primitives", **scope)
    if kind == "hook":
        return adopt.adopt_hook(source["name"], entries, warehouse, "primitives", **scope)
    return adopt.adopt_path(Path(source["path"]), warehouse, "primitives", kind=ADOPT_KIND[kind])


def loadout_changes(warehouse: Path, result: Result) -> dict[Path, tuple[str | None, str]]:
    """New text for each loadout file: {path: (old text or None, new text)}."""
    changes: dict[Path, tuple[str | None, str]] = {}
    for name, lists in sorted(result.added.items()):
        path = warehouse / "loadouts" / f"{name}.toml"
        old = path.read_text("utf-8") if path.is_file() else None
        document = tomlkit.parse(old) if old is not None else tomlkit.document()
        if old is None:
            document["schema_version"] = 1
            document["name"] = name
        changed = old is None
        for key, refs in lists.items():
            current = document.get(key)
            if current is None:
                document[key] = tomlkit.array()
                current = document[key]
            present = {str(r).rpartition(":")[2] for r in current}
            for ref in refs:
                if ref.rpartition(":")[2] not in present and ref not in present:
                    current.append(ref)
                    changed = True
        if changed:
            changes[path] = (old, tomlkit.dumps(document))
    return changes


def kit_changes(result: Result) -> dict[Path, tuple[str | None, str]]:
    changes: dict[Path, tuple[str | None, str]] = {}
    for repo, loadouts in sorted(result.kit_files.items()):
        path = Path(repo) / "kit.toml"
        old = path.read_text("utf-8") if path.is_file() else None
        document = tomlkit.parse(old) if old is not None else tomlkit.document()
        if old is None:
            document["schema_version"] = 1
        current = document.get("loadouts")
        if current is None:
            document["loadouts"] = tomlkit.array()
            current = document["loadouts"]
        added = False
        for loadout in loadouts:
            if loadout not in current:
                current.append(loadout)
                added = True
        if added or old is None:
            changes[path] = (old, tomlkit.dumps(document))
    return changes


def write(changes: dict[Path, tuple[str | None, str]], root: Path | None = None) -> None:
    for path, (_, text) in changes.items():
        tomllib.loads(text)  # never write a file that doesn't parse
        fs.allow(root or path.parent)
        fs.write_atomic(path, text)
