"""Desired state: what the warehouse and a repository's ``kit.toml`` say should be installed.

Pure reads. ``core`` (``loadouts/core.toml``) is the user-scope set; a repository selects
named loadouts in ``kit.toml`` and may add or remove individual items. Resolution is
deterministic: ``extends`` parents come first in the order written, identical items are
deduplicated, and anything ambiguous (a missing reference, a cycle, a repo removing a core
item, conflicting tool filters) is an error rather than a silent "last one wins".
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kitctl.validate import LOADOUT_LISTS, PLUGIN_ID, SLUG, validate

KIT_FILE = "kit.toml"
HARNESSES = ("claude", "codex")
KIT_FIELDS = {"schema_version", "loadouts", "harnesses", "add", "remove", "mcp"}
FILTER_FIELDS = {"enabled_tools", "disabled_tools"}
KIND_ORDER = ("skill", "agent", "command", "rules", "mcp", "hook", "plugin")


class DesiredError(ValueError):
    """The warehouse or kit.toml can't be resolved; the message says where and why."""


@dataclass(frozen=True)
class Primitive:
    kind: str
    name: str
    directory: Path
    harnesses: tuple[str, ...]
    meta: dict[str, Any] = field(compare=False, hash=False)

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Item:
    """One thing to install. ``primitive`` is None for marketplace plugins (name@market)."""

    kind: str
    name: str
    primitive: Primitive | None
    source: str  # the loadout or "kit.toml" that asked for it

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass
class Desired:
    owner: str  # "user" or the repository path
    scope: str  # "user" or "project"
    repo: Path | None
    harnesses: tuple[str, ...]
    items: list[Item]
    filters: dict[str, dict[str, list[str]]]
    loadouts: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "scope": self.scope,
            "repo": str(self.repo) if self.repo else None,
            "harnesses": list(self.harnesses),
            "loadouts": self.loadouts,
            "items": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "source": item.source,
                    "harnesses": list(item.primitive.harnesses) if item.primitive else ["claude"],
                    "path": str(item.primitive.directory) if item.primitive else None,
                }
                for item in self.items
            ],
            "mcp_filters": self.filters,
            "notes": self.notes,
        }


class Warehouse:
    """Validated view of a warehouse checkout: primitives and loadouts by name."""

    def __init__(self, root: Path) -> None:
        self.root = root
        result = validate(root)
        errors = [f for f in result["findings"] if f["severity"] == "error"]
        if errors:
            details = "; ".join(f"{f['message']} [{f['path']}]" for f in errors[:5])
            raise DesiredError(
                f"Warehouse has {len(errors)} validation errors (run `kitctl validate`): {details}"
            )
        self.primitives: dict[tuple[str, str], Primitive] = {}
        folders = {kind: folder for folder, kind in LOADOUT_LISTS.items()}
        for kind, folder in folders.items():
            directory = root / "primitives" / folder
            if not directory.is_dir():
                continue
            for item in sorted(directory.iterdir()):
                document = item / "primitive.toml"
                if not document.is_file():
                    continue
                meta = tomllib.loads(document.read_text("utf-8"))
                harnesses = tuple(h for h in HARNESSES if h in meta.get("harnesses", HARNESSES))
                self.primitives[(kind, item.name)] = Primitive(
                    kind, item.name, item, harnesses or HARNESSES, meta
                )
        self.loadouts: dict[str, dict[str, Any]] = {}
        directory = root / "loadouts"
        if directory.is_dir():
            for document in sorted(directory.glob("*.toml")):
                self.loadouts[document.stem] = tomllib.loads(document.read_text("utf-8"))

    def commit(self) -> str | None:
        """The checked-out commit, read from .git without running git."""
        git = self.root / ".git"
        try:
            head = (git / "HEAD").read_text("utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:]
                loose = git / ref
                if loose.is_file():
                    return loose.read_text("utf-8").strip()
                packed = git / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text("utf-8").splitlines():
                        if line.endswith(" " + ref):
                            return line.split()[0]
                return None
            return head or None
        except OSError:
            return None

    def item(self, kind: str, reference: str, source: str) -> Item:
        if kind == "plugin" and PLUGIN_ID.match(reference):
            return Item("plugin", reference, None, source)
        ref_kind, _, name = reference.rpartition(":")
        if ref_kind and ref_kind != kind:
            raise DesiredError(f"{source}: {reference!r} is not a {kind}")
        primitive = self.primitives.get((kind, name))
        if primitive is None:
            raise DesiredError(f"{source}: no {kind} named {name!r} in {self.root / 'primitives'}")
        return Item(kind, name, primitive, source)

    def resolve(self, names: list[str]) -> list[Item]:
        """Items of the named loadouts and everything they extend, parents first."""
        ordered: list[Item] = []
        seen: set[str] = set()
        visiting: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                raise DesiredError(f"loadout extends cycle: {' -> '.join([*visiting, name])}")
            if name in seen:
                return
            if name not in self.loadouts:
                raise DesiredError(f"No loadout named {name!r} in {self.root / 'loadouts'}")
            visiting.append(name)
            data = self.loadouts[name]
            for parent in data.get("extends", []):
                visit(parent)
            for key, kind in LOADOUT_LISTS.items():
                for reference in data.get(key, []):
                    ordered.append(self.item(kind, reference, f"loadout {name}"))
            visiting.pop()
            seen.add(name)

        for name in names:
            visit(name)
        return _dedupe(ordered)


def _dedupe(items: list[Item]) -> list[Item]:
    unique: dict[str, Item] = {}
    for item in items:
        unique.setdefault(item.ref, item)
    return sorted(unique.values(), key=lambda i: (KIND_ORDER.index(i.kind), i.name))


@dataclass
class KitFile:
    path: Path
    loadouts: list[str]
    harnesses: tuple[str, ...]
    add: dict[str, list[str]]
    remove: dict[str, list[str]]
    filters: dict[str, dict[str, list[str]]]


def _strings(path: Path, label: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DesiredError(f"{path}: {label} must be a list of strings")
    return list(value)


def load_kit(repo: Path) -> KitFile | None:
    path = repo / KIT_FILE
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise DesiredError(f"{path}: {error}") from error
    unknown = sorted(set(data) - KIT_FIELDS)
    if unknown:
        raise DesiredError(f"{path}: unknown fields {', '.join(unknown)}")
    if data.get("schema_version", 1) != 1:
        raise DesiredError(f"{path}: unsupported schema_version {data['schema_version']}")
    loadouts = _strings(path, "loadouts", data.get("loadouts", []))
    for name in loadouts:
        if not SLUG.match(name):
            raise DesiredError(f"{path}: unsafe loadout name {name!r}")
        if name == "core":
            raise DesiredError(f"{path}: core applies at user scope; don't list it in kit.toml")
    harnesses = tuple(_strings(path, "harnesses", data.get("harnesses", list(HARNESSES))))
    if not harnesses or set(harnesses) - set(HARNESSES):
        raise DesiredError(f"{path}: harnesses must be a non-empty subset of claude, codex")
    lists: dict[str, dict[str, list[str]]] = {}
    for section in ("add", "remove"):
        table = data.get(section, {})
        if not isinstance(table, dict) or set(table) - set(LOADOUT_LISTS):
            raise DesiredError(
                f"{path}: [{section}] takes lists named {', '.join(sorted(LOADOUT_LISTS))}"
            )
        lists[section] = {
            key: _strings(path, f"{section}.{key}", value) for key, value in table.items()
        }
    filters: dict[str, dict[str, list[str]]] = {}
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict):
        raise DesiredError(f"{path}: [mcp.<server>] tables expected")
    for server, table in mcp.items():
        if not isinstance(table, dict) or set(table) - FILTER_FIELDS:
            raise DesiredError(f"{path}: [mcp.{server}] takes enabled_tools and disabled_tools")
        filters[server] = {
            key: _strings(path, f"mcp.{server}.{key}", value) for key, value in table.items()
        }
    return KitFile(
        path,
        loadouts,
        tuple(h for h in HARNESSES if h in harnesses),
        lists["add"],
        lists["remove"],
        filters,
    )


def desired_user(warehouse: Warehouse) -> Desired:
    names = ["core"] if "core" in warehouse.loadouts else []
    notes = [] if names else ["No loadouts/core.toml; nothing is installed at user scope."]
    items = warehouse.resolve(names)
    return Desired("user", "user", None, HARNESSES, items, {}, names, notes)


def desired_repo(warehouse: Warehouse, repo: Path) -> Desired:
    repo = repo.expanduser().resolve()
    kit = load_kit(repo)
    if kit is None:
        raise DesiredError(f"No {KIT_FILE} in {repo}; create one listing loadouts to apply")
    source = str(kit.path)
    items = warehouse.resolve(kit.loadouts)
    for key, references in kit.add.items():
        items += [warehouse.item(LOADOUT_LISTS[key], ref, source) for ref in references]
    items = _dedupe(items)
    core = {item.ref for item in desired_user(warehouse).items}
    notes: list[str] = []
    for key, references in kit.remove.items():
        for reference in references:
            removed = warehouse.item(LOADOUT_LISTS[key], reference, source)
            if removed.ref in core:
                raise DesiredError(
                    f"{source}: can't remove {removed.ref}; it comes from core (user scope). "
                    "Change loadouts/core.toml instead."
                )
            if removed.ref not in {item.ref for item in items}:
                notes.append(f"remove {removed.ref}: not selected by any loadout")
            items = [item for item in items if item.ref != removed.ref]
    duplicates = [item for item in items if item.ref in core]
    for item in duplicates:
        notes.append(f"{item.ref} is already installed at user scope by core; skipped here")
    items = [item for item in items if item.ref not in core]
    servers = {item.name for item in items if item.kind == "mcp"}
    for server, table in kit.filters.items():
        if server not in servers:
            where = "core" if f"mcp:{server}" in core else "this repository's loadouts"
            raise DesiredError(f"{source}: [mcp.{server}] filters a server not in {where}")
        overlap = set(table.get("enabled_tools", [])) & set(table.get("disabled_tools", []))
        if overlap:
            raise DesiredError(
                f"{source}: [mcp.{server}] both enables and disables {', '.join(sorted(overlap))}"
            )
    return Desired(
        str(repo), "project", repo, kit.harnesses, items, kit.filters, kit.loadouts, notes
    )


def server_config(primitive: Primitive) -> dict[str, Any]:
    """The adopted server definition (``server.json`` holds ``{name: config}``)."""
    data = json.loads((primitive.directory / "server.json").read_text("utf-8"))
    if isinstance(data, dict) and len(data) == 1:
        (config,) = data.values()
        if isinstance(config, dict):
            return config
    raise DesiredError(f"{primitive.directory / 'server.json'}: expected {{name: {{...}}}}")
