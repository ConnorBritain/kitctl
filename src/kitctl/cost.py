"""Always-on listing cost: what a set of loadouts puts in front of the model in every session.

Each session lists the instruction block, every model-invocable skill (`name: description`)
and every agent before any work starts. This module counts that text with a fixed estimate,
the same one Gestalt uses (tools/lib/tokens.mjs), so a budget means the same number in both.
Scripts count; nothing here asks a model, starts an MCP server, or writes a file.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from kitctl.model import (
    AGENT_FILES,
    BUDGET_KEYS,
    KIND_DIRS,
    ModelError,
    Primitive,
    frontmatter,
    parse_primitive,
)
from kitctl.resolve import effective, resolve

METHOD = "estimate: ceil(chars / 4)"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def listing_line(name: str, description: str) -> str:
    """One listed skill or agent, as Gestalt counts it."""
    return f"{name}: {description}"


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _skill_front(path: Path) -> dict[str, Any] | None:
    text = _read(path) if path.is_file() else None
    return None if text is None else frontmatter(text)


def _hidden(front: dict[str, Any]) -> bool:
    """A skill the model cannot invoke is not listed, so it costs nothing."""
    return front.get("disable-model-invocation") is True


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class Listing:
    tokens: int = 0
    skills: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)
    blocks: set[tuple[str, str]] = field(default_factory=set)
    not_measured: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message not in self.not_measured:
            self.not_measured.append(message)

    def skill(self, key: str, name: str, description: str) -> None:
        """Count a listed skill once per key (its native name, plugin-qualified)."""
        if key not in self.skills:
            self.skills.add(key)
            self.tokens += estimate_tokens(listing_line(name, description))

    def result(self) -> dict[str, Any]:
        return {
            "method": METHOD,
            "tokens": self.tokens,
            "skills": len(self.skills),
            "not_measured": self.not_measured,
        }


class _Measure:
    def __init__(
        self,
        warehouse: Path,
        harness: str,
        installed: Mapping[str, str],
        plugin_ids: Mapping[str, str],
    ) -> None:
        self.warehouse = warehouse
        self.harness = harness
        self.installed = installed
        self.plugin_ids = plugin_ids
        self.loaded: dict[str, Primitive] = {}
        self.listing = Listing()

    def primitive(self, ref: str) -> Primitive | None:
        if ref not in self.loaded:
            try:
                self.loaded[ref] = parse_primitive(self.warehouse, ref)
            except ModelError:
                return None
        return self.loaded[ref]

    def assets(self, primitive: Primitive) -> Path:
        return (
            self.warehouse
            / "primitives"
            / KIND_DIRS[primitive.kind]
            / PurePosixPath(primitive.name)
        )

    def native_skill(self, primitive: Primitive) -> str:
        return (
            primitive.harnesses.get(self.harness, {}).get("skill")
            or primitive.name.rsplit("/", 1)[-1]
        )

    def skill(self, primitive: Primitive, disabled: set[str]) -> None:
        name = self.native_skill(primitive)
        description = _text(primitive.data.get("description"))
        front = (
            None
            if primitive.data.get("external")
            else _skill_front(self.assets(primitive) / "SKILL.md")
        )
        if front is not None:
            if _hidden(front):
                return
            name = _text(front.get("name")) or name
            description = _text(front.get("description")) or description
        if name not in disabled:
            self.listing.skill(name, name, description)

    def agent(self, primitive: Primitive) -> None:
        if self.harness not in AGENT_FILES or primitive.data.get("external"):
            return  # Cursor gets a gap, not a file; an external agent is not Kit's to count
        name = primitive.name.rsplit("/", 1)[-1]
        if name in self.listing.agents:
            return
        self.listing.agents.add(name)
        description = _text(primitive.data.get("description"))
        text = _read(self.assets(primitive) / AGENT_FILES[self.harness])
        if text is not None:
            if self.harness == "codex":
                try:
                    data: dict[str, Any] = tomllib.loads(text)
                except tomllib.TOMLDecodeError:
                    data = {}
            else:
                data = frontmatter(text)
            description = _text(data.get("description")) or description
        self.listing.tokens += estimate_tokens(listing_line(name, description))

    def plugin(self, primitive: Primitive, disabled: set[str]) -> None:
        native = self.plugin_ids.get(primitive.identity)
        path = self.installed.get(native) if native and self.harness == "claude" else None
        root = Path(path) if path else None
        if root is None or not root.is_dir():
            where = (
                "not installed in Claude Code"
                if self.harness == "claude"
                else f"Kit cannot see {self.harness}'s installed plugins"
            )
            declared = primitive.data.get("skills") or []
            count = f", declares {len(declared)}" if declared else ""
            self.listing.note(f"{primitive.identity} skills: {where}{count}")
            return
        prefix = (native or "").split("@", 1)[0]
        for manifest in sorted((root / "skills").glob("*/SKILL.md")):
            front = _skill_front(manifest)
            if front is None or _hidden(front):
                continue
            name = _text(front.get("name")) or manifest.parent.name
            if name in disabled or f"{prefix}:{name}" in disabled:
                continue
            self.listing.skill(f"{prefix}:{name}", name, _text(front.get("description")))

    def target(self, target: str, scope: str) -> None:
        try:
            plan = resolve(self.warehouse, target, self.harness, scope=scope)
            merged = effective(self.warehouse, target)
        except ModelError:
            self.listing.note(f"{target}: does not resolve (`kitctl validate` says why)")
            return
        block = plan["instructions"]
        if block and (block["target"], block["marker"]) not in self.listing.blocks:
            self.listing.blocks.add((block["target"], block["marker"]))
            self.listing.tokens += estimate_tokens(block["text"])
        disabled: set[str] = set()
        for record in plan["disables"]:
            primitive = self.primitive(record["identity"])
            if primitive is not None and primitive.kind == "skill":
                disabled.add(self.native_skill(primitive))
        servers, tools = 0, 0
        for item in plan["primitives"]:
            primitive = self.primitive(item["identity"])
            if primitive is None:
                continue
            if primitive.kind == "skill":
                self.skill(primitive, disabled)
            elif primitive.kind == "agent":
                self.agent(primitive)
            elif primitive.kind == "plugin":
                self.plugin(primitive, disabled)
            elif primitive.kind == "mcp":
                servers += 1
                tools += sum(
                    len(entry.get("enabled_tools", []))
                    for entry, _ in merged.mcp.values()
                    if entry["primitive"] == primitive.identity
                )
        if servers:
            named = f", {tools} named enabled tools" if tools else ""
            self.listing.note(
                f"{target}: {servers} MCP servers{named} (tool lists need a running server; "
                "doctor never starts one)"
            )


def listing_cost(
    warehouse: Path,
    targets: list[tuple[str, str]],
    harness: str,
    installed: Mapping[str, str] | None = None,
    plugin_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The always-on listing of every (target, scope) together, for one harness.

    installed maps a Claude Code plugin id to its install path; plugin_ids maps a plugin
    primitive to its native id on this harness.
    """
    measure = _Measure(warehouse, harness, installed or {}, plugin_ids or {})
    for target, scope in targets:
        measure.target(target, scope)
    return measure.listing.result()


def strictest_budget(warehouse: Path, targets: list[str]) -> dict[str, tuple[int, str]]:
    """The lowest declared number per budget key across targets, with who declared it."""
    budget: dict[str, tuple[int, str]] = {}
    for target in targets:
        try:
            merged = effective(warehouse, target)
        except ModelError:
            continue
        for key in BUDGET_KEYS:
            if key in merged.budget and (
                key not in budget or merged.budget[key][0] < budget[key][0]
            ):
                budget[key] = merged.budget[key]
    return budget


def over_budget(cost: dict[str, Any], budget: dict[str, tuple[int, str]]) -> list[str]:
    """One message per budget number the listing exceeds."""
    messages = []
    for key, unit in (("tokens", "tokens"), ("skills", "listed skills")):
        if key in budget and cost[key] > budget[key][0]:
            limit, source = budget[key]
            messages.append(
                f"{cost[key]} {unit} is over the budget of {limit} (from {source}) by "
                f"{cost[key] - limit}"
            )
    return messages
