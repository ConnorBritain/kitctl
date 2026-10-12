"""`kitctl marketplace build`: a Claude Code plugin marketplace generated in the warehouse.

``<warehouse>/marketplace/`` gets ``.claude-plugin/marketplace.json`` and one plugin per
entry: each adopted plugin primitive (copied as is) and one ``loadout-<name>`` plugin per
loadout bundling its skills, agents, commands, hooks, and MCP servers. Plugin versions
carry a content hash, so a changed loadout is a new version. Everything under
``marketplace/`` is generated; edits there are overwritten.

Codex plugin generation is a documented gap until its packaging format is verified.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from kitctl import fs
from kitctl.compile import _neutral, claude_server, source_files
from kitctl.desired import DesiredError, Item, Warehouse, server_config
from kitctl.safemeta import canonical_hash

NAME = "kitctl"


class MarketplaceError(ValueError):
    pass


def _plugin_files(item_name: str, items: list[Item], notes: list[str]) -> dict[str, bytes]:
    """Files of a loadout plugin, keyed by relative path."""
    files: dict[str, bytes] = {}
    hooks: dict[str, list[dict[str, Any]]] = {}
    servers: dict[str, Any] = {}
    for item in items:
        primitive = item.primitive
        if primitive is None:
            notes.append(f"{item_name}: {item.ref} is a marketplace plugin; plugins can't nest")
            continue
        if "claude" not in primitive.harnesses:
            continue
        source = primitive.directory / "files"
        if item.kind == "skill":
            for rel, path in source_files(source).items():
                files[f"skills/{item.name}/{rel}"] = path.read_bytes()
        elif item.kind in ("agent", "command"):
            for rel, path in source_files(source).items():
                if rel.endswith(".md") and "/" not in rel:
                    files[f"{item.kind}s/{rel}"] = path.read_bytes()
        elif item.kind == "mcp":
            servers[item.name] = claude_server(_neutral(server_config(primitive)))
        elif item.kind == "hook":
            spec = primitive.meta.get("hook")
            if not isinstance(spec, dict):
                notes.append(f"{item_name}: {item.ref} has no [hook] table; skipped")
                continue
            command = spec.get("command", "{script}")
            if spec.get("script"):
                for rel, path in source_files(source).items():
                    files[f"hooks/{item.name}/{rel}"] = path.read_bytes()
                where = f'"${{CLAUDE_PLUGIN_ROOT}}/hooks/{item.name}/{spec["script"]}"'
                command = command.replace("{script}", where)
            handler: dict[str, Any] = {"type": "command", "command": command}
            if "timeout" in spec:
                handler["timeout"] = spec["timeout"]
            group: dict[str, Any] = {"hooks": [handler]}
            if spec.get("matcher"):
                group = {"matcher": spec["matcher"], **group}
            hooks.setdefault(spec["event"], []).append(group)
        elif item.kind == "rules":
            notes.append(f"{item_name}: {item.ref} skipped; plugins don't carry rules files")
    if servers:
        files[".mcp.json"] = (json.dumps({"mcpServers": servers}, indent=2) + "\n").encode()
    if hooks:
        files["hooks/hooks.json"] = (json.dumps({"hooks": hooks}, indent=2) + "\n").encode()
    return files


def _version(files: dict[str, bytes]) -> str:
    digest = canonical_hash({rel: data.hex() for rel, data in sorted(files.items())})
    return f"0.0.0-kitctl.{digest[:12]}"


def build(warehouse: Warehouse) -> dict[str, Any]:
    """Plan the marketplace: {relative path: bytes}, plus entries and notes."""
    notes: list[str] = []
    files: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for (kind, name), primitive in sorted(warehouse.primitives.items()):
        if kind != "plugin":
            continue
        source = primitive.directory / "files"
        manifest_path = source / ".claude-plugin" / "plugin.json"
        if not manifest_path.is_file():
            notes.append(f"plugin {name}: no .claude-plugin/plugin.json; not a Claude plugin")
            continue
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("name") != name:
            raise MarketplaceError(
                f"plugin {name}: manifest name {manifest.get('name')!r} must equal the "
                "primitive name (it is the install id)"
            )
        for rel, path in source_files(source).items():
            files[f"plugins/{name}/{rel}"] = path.read_bytes()
        entry = {"name": name, "source": f"./plugins/{name}"}
        for key in ("description", "version"):
            if isinstance(manifest.get(key), str):
                entry[key] = manifest[key]
        entries.append(entry)
    for loadout, data in sorted(warehouse.loadouts.items()):
        plugin = f"loadout-{loadout}"
        try:
            items = warehouse.resolve([loadout])
        except DesiredError as error:
            raise MarketplaceError(str(error)) from error
        content = _plugin_files(plugin, items, notes)
        if not content:
            notes.append(f"{plugin}: nothing Claude Code can load; skipped")
            continue
        description = data.get("description") or f"kitctl loadout {loadout}"
        version = _version(content)
        manifest = {
            "name": plugin,
            "version": version,
            "description": description,
            "author": {"name": "kitctl"},
        }
        content[".claude-plugin/plugin.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        for rel, blob in content.items():
            files[f"plugins/{plugin}/{rel}"] = blob
        entries.append(
            {
                "name": plugin,
                "source": f"./plugins/{plugin}",
                "description": description,
                "version": version,
            }
        )
    marketplace = {
        "name": NAME,
        "owner": {"name": "kitctl"},
        "description": "Generated by `kitctl marketplace build` from your warehouse.",
        "plugins": entries,
    }
    files[".claude-plugin/marketplace.json"] = (json.dumps(marketplace, indent=2) + "\n").encode()
    files["README.md"] = (
        b"# Generated marketplace\n\nWritten by `kitctl marketplace build`; edits here are "
        b"overwritten. Change primitives or loadouts and rebuild.\n"
    )
    return {"files": files, "plugins": [e["name"] for e in entries], "notes": notes}


def write(warehouse: Warehouse, planned: dict[str, Any]) -> tuple[int, int]:
    """Replace ``<warehouse>/marketplace`` with the planned files; (written, removed)."""
    root = warehouse.root / "marketplace"
    fs.allow(warehouse.root)
    existing = source_files(root)
    written = removed = 0
    for rel, data in planned["files"].items():
        target = root / rel
        if target.is_file() and target.read_bytes() == data:
            continue
        fs.write_bytes(target, data, 0o755 if data.startswith(b"#!") else 0o644)
        written += 1
    for rel, path in existing.items():
        if rel not in planned["files"]:
            fs.remove(path)
            removed += 1
    for directory in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)
    ):
        if not any(directory.iterdir()):
            fs.check(directory)
            shutil.rmtree(directory)
    return written, removed
