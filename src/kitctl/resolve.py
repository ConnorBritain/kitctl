"""Resolve a loadout (optionally loadout:profile) into one harness's effective config.

Pure: reads warehouse documents and returns a plan-shaped dictionary. Nothing
here writes files, runs tools, or reads native agent config; an optional
observed set (identities enabled outside Kit, from inventory) is passed in.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kitctl.harness import HARNESS_FACTS, Fact, Harness
from kitctl.model import (
    HARNESSES,
    REF_RE,
    Loadout,
    ModelError,
    Primitive,
    Problem,
    is_traversal,
    parse_loadout,
    parse_primitive,
    primitive_path,
)

RESOLVE_FORMAT = 1
SET_FIELDS = ("skills", "hooks", "plugins", "marketplaces")
TOOL_FIELDS = ("requires", "optional")
PACKAGE_RUNNERS = {"npx": ["-y"], "bunx": [], "pnpx": [], "uvx": []}


@dataclass
class Merged:
    """Effective selections with the loadout layer that contributed each one."""

    sets: dict[str, dict[str, str]] = field(default_factory=lambda: {k: {} for k in SET_FIELDS})
    mcp: dict[str, tuple[dict[str, Any], str]] = field(default_factory=dict)
    tools: dict[str, dict[str, str]] = field(default_factory=lambda: {k: {} for k in TOOL_FIELDS})
    slots: dict[str, tuple[str, str]] = field(default_factory=dict)
    disable: list[tuple[str, str]] = field(default_factory=list)
    setup: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    instructions: list[tuple[str, str]] = field(default_factory=list)


def split_target(target: str) -> tuple[str, str | None]:
    name, _, profile = target.partition(":")
    return name, profile or None


def linearize(root: Path, name: str) -> list[Loadout]:
    """Parents first, in declared order, each loadout once. Cycles and gaps are errors."""
    order: list[Loadout] = []
    seen: set[str] = set()
    problems: list[Problem] = []

    def visit(current: str, stack: list[str]) -> None:
        if current in stack:
            cycle = " -> ".join([*stack[stack.index(current) :], current])
            problems.append(Problem(f"loadouts/{current}.yaml", "cycle", f"extends cycle: {cycle}"))
            return
        if current in seen:
            return
        try:
            loadout = parse_loadout(root, current)
        except ModelError as error:
            if stack and all(p.code == "missing" for p in error.problems):
                problems.append(
                    Problem(
                        f"loadouts/{stack[-1]}.yaml",
                        "missing",
                        f"{stack[-1]} extends missing loadout {current!r}",
                    )
                )
            else:
                problems.extend(error.problems)
            return
        for parent in loadout.extends:
            visit(parent, [*stack, current])
        if current not in seen:
            seen.add(current)
            order.append(loadout)

    visit(name, [])
    if problems:
        raise ModelError(sorted(set(problems)))
    return order


def _merge(layers: list[Loadout], problems: list[Problem]) -> Merged:
    merged = Merged()
    for layer in layers:
        where = layer.path
        for key in SET_FIELDS:
            for ref in getattr(layer, key):
                merged.sets[key].setdefault(ref, layer.name)
        for key in TOOL_FIELDS:
            for ref in getattr(layer, key):
                merged.tools[key].setdefault(ref, layer.name)
        for server, entry in layer.mcp.items():
            if server in merged.mcp and merged.mcp[server][0] != entry:
                other = merged.mcp[server][1]
                problems.append(
                    Problem(
                        f"{where}.mcp.{server}",
                        "override",
                        f"mcp server {server!r} is defined differently by {other} and "
                        f"{layer.name}; incompatible overrides are rejected",
                    )
                )
            merged.mcp.setdefault(server, (entry, layer.name))
        for slot, ref in layer.slots.items():
            if slot in merged.slots and merged.slots[slot][0] != ref:
                other_ref, other = merged.slots[slot]
                problems.append(
                    Problem(
                        f"{where}.slots.{slot}",
                        "slot-conflict",
                        f"slot {slot!r} is assigned to {other_ref} by {other} and to {ref} by "
                        f"{layer.name}",
                    )
                )
            merged.slots.setdefault(slot, (ref, layer.name))
        for ref in layer.disable:
            if (ref, layer.name) not in merged.disable:
                merged.disable.append((ref, layer.name))
        for step in layer.setup:
            if all(step != existing for existing, _ in merged.setup):
                merged.setup.append((step, layer.name))
        if layer.instructions.strip():
            merged.instructions.append((layer.name, layer.instructions.strip()))
    for ref in merged.tools["optional"].copy():
        if ref in merged.tools["requires"]:
            del merged.tools["optional"][ref]
    return merged


def _apply_profile(top: Loadout, profile: str, merged: Merged, problems: list[Problem]) -> None:
    spec = top.profiles.get(profile)
    where = f"{top.path}.profiles.{profile}"
    if spec is None:
        known = ", ".join(sorted(top.profiles)) or "none"
        raise ModelError(
            [Problem(top.path, "missing", f"no profile {profile!r} (profiles: {known})")]
        )
    fields = spec.fields
    for key in SET_FIELDS:
        if key in fields:
            current = merged.sets[key]
            for ref in fields[key]:
                if ref not in current:
                    problems.append(
                        Problem(f"{where}.{key}", "profile", f"{ref} is not in {top.name}")
                    )
            merged.sets[key] = {ref: current[ref] for ref in fields[key] if ref in current}
    for key in TOOL_FIELDS:
        if key in fields:
            available = {**merged.tools["requires"], **merged.tools["optional"]}
            for ref in fields[key]:
                if ref not in available:
                    problems.append(
                        Problem(f"{where}.{key}", "profile", f"{ref} is not in {top.name}")
                    )
            merged.tools[key] = {ref: available[ref] for ref in fields[key] if ref in available}
    if "requires" in fields and "optional" not in fields:
        for ref in list(merged.tools["optional"]):
            if ref in merged.tools["requires"]:
                del merged.tools["optional"][ref]
    if "optional" in fields and "requires" not in fields:
        for ref in fields["optional"]:
            merged.tools["requires"].pop(ref, None)
    if "mcp" in fields:
        selected = {}
        for server, entry in fields["mcp"].items():
            base = merged.mcp.get(server)
            if base is None or base[0]["primitive"] != entry["primitive"]:
                problems.append(
                    Problem(f"{where}.mcp.{server}", "profile", f"{server} is not in {top.name}")
                )
            else:
                selected[server] = (entry, base[1])
        merged.mcp = selected
    if "setup" in fields:
        merged.setup = [(step, top.name) for step in fields["setup"]]
    if fields.get("instructions_append", "").strip():
        merged.instructions.append((f"{top.name}:{profile}", fields["instructions_append"].strip()))


def _load(root: Path, refs: set[str], problems: list[Problem]) -> dict[str, Primitive]:
    loaded: dict[str, Primitive] = {}
    for ref in sorted(refs):
        try:
            loaded[ref] = parse_primitive(root, ref)
        except ModelError as error:
            problems.extend(error.problems)
    return loaded


def _gap(
    harness: str,
    subject: str,
    capability: str,
    severity: str,
    reason: str,
    remedy: str | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    return {
        "harness": harness,
        "subject": subject,
        "capability": capability,
        "severity": severity,
        "reason": reason,
        "remedy": remedy,
        "fallback": fallback,
    }


def _effect(
    target: str,
    fmt: str,
    key: str,
    value: Any,
    source: str,
    layer: str,
    fact: Fact,
) -> dict[str, Any]:
    return {
        "target": target,
        "format": fmt,
        "key": key,
        "value": value,
        "source": source,
        "layer": layer,
        "owned": True,
        "evidence": fact.evidence,
    }


def _launch(primitive: Primitive) -> tuple[str, list[str]]:
    data = primitive.data
    command = str(data["command"])
    args = [str(a) for a in data.get("args", [])]
    pin = data.get("pin")
    if pin and command in PACKAGE_RUNNERS:
        package = pin.get("package", primitive.name)
        args = [*PACKAGE_RUNNERS[command], f"{package}@{pin['version']}", *args]
    return command, args


def _github_slug(url: str) -> str | None:
    prefix = "https://github.com/"
    if url.startswith(prefix):
        slug = url[len(prefix) :].removesuffix(".git").strip("/")
        if slug.count("/") == 1:
            return slug
    return None


class Compiler:
    """Turns the effective primitive set into native effects and gaps for one harness."""

    def __init__(self, harness: Harness, profile: str | None) -> None:
        self.harness = harness
        self.profile = profile
        self.effects: list[dict[str, Any]] = []
        self.gaps: list[dict[str, Any]] = []
        self.soft_rules: list[str] = []

    @property
    def name(self) -> str:
        return self.harness.name

    def fact(self, name: str) -> Fact:
        return self.harness.facts[name]

    def add(
        self, target: str, fmt: str, key: str, value: Any, source: str, layer: str, fact: str
    ) -> None:
        self.effects.append(_effect(target, fmt, key, value, source, layer, self.fact(fact)))

    def gap(
        self,
        subject: str,
        capability: str,
        severity: str,
        reason: str,
        remedy: str | None = None,
        soft_rule: str | None = None,
    ) -> None:
        if soft_rule:
            self.soft_rules.append(soft_rule)
        fallback = "managed instruction block (soft rule)" if soft_rule else None
        self.gaps.append(_gap(self.name, subject, capability, severity, reason, remedy, fallback))

    def native(self, primitive: Primitive, key: str) -> str | None:
        return primitive.harnesses.get(self.name, {}).get(key)

    def marketplace_name(self, primitive: Primitive) -> str:
        return self.native(primitive, "id") or primitive.name.rsplit("/", 1)[-1]

    def plugin_id(self, plugin: Primitive, loaded: dict[str, Primitive]) -> str | None:
        explicit = self.native(plugin, "id")
        if explicit:
            return explicit
        market_ref = plugin.data.get("marketplace")
        if not market_ref or market_ref not in loaded:
            return None
        name = plugin.data.get("plugin_name", plugin.name.rsplit("/", 1)[-1])
        return f"{name}@{self.marketplace_name(loaded[market_ref])}"

    def marketplace(self, primitive: Primitive, layer: str) -> None:
        source = primitive.data.get("source", {})
        url, sha, tag = source.get("git"), source.get("sha"), source.get("tag")
        name = self.marketplace_name(primitive)
        if self.name == "claude":
            native: dict[str, Any] = {"source": "git", "url": url}
            if tag:
                native["ref"] = tag
            if source.get("path"):
                native["path"] = source["path"]
            self.add(
                ".claude/settings.json",
                "json",
                f"/extraKnownMarketplaces/{name}",
                {"source": native},
                primitive.identity,
                layer,
                "marketplace_file",
            )
            if not tag:
                fact = self.fact("marketplace_sha_pin")
                self.gap(
                    primitive.identity,
                    "marketplace_sha_pin",
                    "unsupported",
                    f"pinned to commit {sha}, but {fact.note}; the marketplace would track the "
                    "default branch",
                    remedy="tag the pinned commit upstream and set source.tag in the primitive",
                )
            return
        slug = _github_slug(url) or url
        ref = tag or sha
        self.gap(
            primitive.identity,
            "marketplace_file",
            "manual-step",
            self.fact("marketplace_file").note,
            remedy=f"codex plugin marketplace add {slug} --ref {ref}",
        )
        if not tag:
            self.gap(
                primitive.identity,
                "marketplace_sha_pin",
                "unverified",
                self.fact("marketplace_sha_pin").note,
            )

    def plugin(self, primitive: Primitive, layer: str, loaded: dict[str, Primitive]) -> None:
        native = self.plugin_id(primitive, loaded)
        if native is None:
            self.gap(
                primitive.identity,
                "plugin_enable",
                "unsupported",
                "no native plugin id and no marketplace to derive one from",
            )
            return
        if self.name == "claude":
            self.add(
                ".claude/settings.json",
                "json",
                f"/enabledPlugins/{native}",
                True,
                primitive.identity,
                layer,
                "plugin_enable",
            )
        else:
            self.add(
                ".codex/config.toml",
                "toml",
                f'plugins."{native}".enabled',
                True,
                primitive.identity,
                layer,
                "plugin_enable",
            )
        if primitive.data.get("hook_provides") and self.fact("project_trust_required").supported:
            self.gap(
                primitive.identity,
                "hook_trust",
                "trust-required",
                "plugin-bundled hooks are skipped until the user reviews and trusts them; "
                "Kit never writes trust",
                remedy="review and trust the hook in Codex (/hooks); `kitctl doctor` reports it",
            )

    def mcp(self, server: str, entry: dict[str, Any], primitive: Primitive, layer: str) -> None:
        data = primitive.data
        env = data.get("env", {})
        filters = {k: entry[k] for k in ("enabled_tools", "disabled_tools") if k in entry}
        if self.name == "claude":
            if data.get("transport", "stdio") == "http":
                value: dict[str, Any] = {"type": "http", "url": data["url"]}
                headers = data.get("headers", {})
                if headers:
                    value["headers"] = {k: f"${{{v['env']}}}" for k, v in sorted(headers.items())}
            else:
                command, args = _launch(primitive)
                value = {"type": "stdio", "command": command, "args": args}
                value["env"] = {k: f"${{{v['env']}}}" for k, v in sorted(env.items())}
            self.add(
                ".mcp.json",
                "json",
                f"/mcpServers/{server}",
                value,
                primitive.identity,
                layer,
                "mcp_project_file",
            )
            if filters:
                self.gap(
                    f"mcp:{server}",
                    "mcp_tool_filter",
                    "unsupported",
                    "Claude permissions can deny tools but are not a server exposure filter",
                    remedy="use a filtering proxy (separately reviewed) or drop the filter",
                )
            return
        if data.get("transport", "stdio") == "http":
            value = {"url": data["url"]}
            if data.get("headers"):
                value["env_http_headers"] = {
                    k: v["env"] for k, v in sorted(data["headers"].items())
                }
        else:
            command, args = _launch(primitive)
            value = {"command": command, "args": args}
            renamed = sorted(k for k, v in env.items() if k != v["env"])
            if renamed:
                self.gap(
                    f"mcp:{server}",
                    "mcp_env_rename",
                    "unsupported",
                    f"env_vars forwards variables by name; cannot map {', '.join(renamed)}",
                    remedy="use the same variable name in the primitive",
                )
            forwarded = sorted(k for k, v in env.items() if k == v["env"])
            if forwarded:
                value["env_vars"] = forwarded
        value.update(filters)
        self.add(
            ".codex/config.toml",
            "toml",
            f"mcp_servers.{server}",
            value,
            primitive.identity,
            layer,
            "mcp_project_file",
        )

    def hook(self, primitive: Primitive, layer: str) -> None:
        data = primitive.data
        native = {
            "matcher": data["matcher"],
            "hooks": [
                {
                    "type": "command",
                    "command": shlex.join(str(part) for part in data["command"]),
                    **({"timeout": data["timeout"]} if "timeout" in data else {}),
                }
            ],
        }
        if self.name == "claude":
            self.add(
                ".claude/settings.json",
                "json",
                f"/hooks/{data['event']}/-",
                native,
                primitive.identity,
                layer,
                "plugin_hooks",
            )
            return
        self.add(
            ".codex/hooks.json",
            "json",
            f"/hooks/{data['event']}/-",
            native,
            primitive.identity,
            layer,
            "plugin_hooks",
        )
        self.gap(
            primitive.identity,
            "hook_trust",
            "trust-required",
            "Codex skips hooks until the user reviews and trusts their definition hash; "
            "Kit stages the hook and never writes trust",
            remedy="review the hook in Codex (/hooks); `kitctl doctor` reports pending trust",
        )

    def skill(self, primitive: Primitive, layer: str) -> None:
        name = primitive.name.rsplit("/", 1)[-1]
        target = f".claude/skills/{name}/" if self.name == "claude" else f".agents/skills/{name}/"
        self.add(
            target, "directory", "", primitive.content_hash, primitive.identity, layer, "skill_dir"
        )

    def disable(self, primitive: Primitive, layer: str, loaded: dict[str, Primitive]) -> None:
        subject = primitive.identity
        soft = f"Do not use {primitive.name} in this repository."
        if not primitive.supports(self.name) and not self.native(primitive, "id"):
            self.gap(
                subject,
                "disable",
                "unsupported",
                f"no {self.name} identity is declared for {subject}",
                soft_rule=soft,
            )
            return
        if primitive.kind == "plugin":
            native = self.plugin_id(primitive, loaded)
            if native is None:
                self.gap(
                    subject,
                    "plugin_disable",
                    "unsupported",
                    f"no native {self.name} plugin id declared",
                    soft_rule=soft,
                )
            elif self.name == "claude":
                self.add(
                    ".claude/settings.json",
                    "json",
                    f"/enabledPlugins/{native}",
                    False,
                    subject,
                    layer,
                    "plugin_disable",
                )
            else:
                self.add(
                    ".codex/config.toml",
                    "toml",
                    f'plugins."{native}".enabled',
                    False,
                    subject,
                    layer,
                    "plugin_disable",
                )
            return
        if primitive.kind == "skill":
            fact = self.fact("skill_disable")
            container = primitive.data.get("plugin")
            if self.name == "claude":
                native = self.native(primitive, "skill") or primitive.name.rsplit("/", 1)[-1]
                self.add(
                    ".claude/settings.json",
                    "json",
                    f"/skillOverrides/{native}",
                    "off",
                    subject,
                    layer,
                    "skill_disable",
                )
                where = f" inside {container}" if container else ""
                self.gap(
                    subject,
                    "skill_disable",
                    "unverified",
                    f"single-skill disable{where}: {fact.note}",
                    remedy="sandbox-probe skillOverrides before the first apply",
                    soft_rule=soft,
                )
            else:
                self.gap(
                    subject,
                    "skill_disable",
                    "unsupported",
                    f"{fact.note}; a project loadout cannot disable a single skill",
                    remedy="disable it in user config by hand, or disable the whole plugin",
                    soft_rule=soft,
                )
            return
        self.gap(
            subject,
            "disable",
            "unsupported",
            f"disabling a {primitive.kind} is not generated yet",
            soft_rule=soft,
        )


def resolve(
    root: Path,
    target: str,
    harness: str,
    observed: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve target ("loadout" or "loadout:profile") for one harness."""
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness {harness!r}")
    name, profile = split_target(target)
    if is_traversal(name):
        raise ModelError([Problem(name, "path-traversal", f"reference escapes: {name!r}")])
    layers = linearize(root, name)
    top = layers[-1]
    problems: list[Problem] = []
    warnings: list[Problem] = []
    merged = _merge(layers, problems)
    if profile:
        _apply_profile(top, profile, merged, problems)
    if problems:
        raise ModelError(sorted(set(problems)))

    refs: set[str] = set()
    for key in SET_FIELDS:
        refs.update(merged.sets[key])
    for key in TOOL_FIELDS:
        refs.update(merged.tools[key])
    refs.update(entry["primitive"] for entry, _ in merged.mcp.values())
    refs.update(ref for ref, _ in merged.slots.values())
    refs.update(ref for ref, _ in merged.disable)
    loaded = _load(root, refs, problems)
    for ref in sorted(loaded):
        market = loaded[ref].data.get("marketplace")
        if loaded[ref].kind == "plugin" and market and market not in loaded:
            refs.add(market)
    loaded.update(_load(root, refs - set(loaded), problems))
    for ref in sorted((observed or set()) - set(loaded)):
        # Unmanaged identities are known only if the warehouse describes them.
        if REF_RE.match(ref) and primitive_path(root, ref).is_file():
            loaded.update(_load(root, {ref}, problems))
    if problems:
        raise ModelError(sorted(set(problems)))

    facts = HARNESS_FACTS[harness]
    compiler = Compiler(facts, profile)

    # Active identities and the layer that enabled them.
    active: dict[str, str] = {}
    for key in SET_FIELDS:
        active.update(merged.sets[key])
    mcp_by_ref = {
        entry["primitive"]: (server, layer) for server, (entry, layer) in merged.mcp.items()
    }
    for ref, (_server, layer) in mcp_by_ref.items():
        active.setdefault(ref, layer)
    order = [layer.name for layer in layers]

    # Disables: override an outer layer, or compile a native disable for external state.
    disables: list[dict[str, Any]] = []
    external_disables: list[tuple[Primitive, str]] = []
    for ref, layer in merged.disable:
        primitive = loaded[ref]
        record: dict[str, Any] = {"identity": ref, "layer": layer}
        enabled_by = active.get(ref)
        if enabled_by is not None:
            if order.index(enabled_by) >= order.index(layer):
                problems.append(
                    Problem(
                        f"loadouts/{layer}.yaml.disable",
                        "disable-conflict",
                        f"{layer} disables {ref}, which {enabled_by} enables",
                    )
                )
                continue
            record.update(status="overrides", enabled_by=enabled_by)
            del active[ref]
            for key in SET_FIELDS:
                merged.sets[key].pop(ref, None)
            if ref in mcp_by_ref:
                merged.mcp.pop(mcp_by_ref[ref][0], None)
        elif observed is not None and ref not in observed:
            record["status"] = "stale"
            warnings.append(
                Problem(
                    f"loadouts/{layer}.yaml.disable",
                    "stale-disable",
                    f"{ref} is disabled but nothing enables it",
                    "warning",
                )
            )
        else:
            record["status"] = "external" if observed is not None else "external-unverified"
            external_disables.append((primitive, layer))
        disables.append(record)
    if problems:
        raise ModelError(sorted(set(problems)))

    # Harness support and capability-conditional primitives.
    skipped: list[dict[str, str]] = []
    for ref in sorted(active):
        primitive = loaded[ref]
        if not primitive.supports(harness):
            skipped.append({"identity": ref, "reason": f"harnesses excludes {harness}"})
            del active[ref]
            continue
        capability = primitive.data.get("unless_capability")
        if capability:
            fact = facts.facts.get(capability)
            if fact is None:
                problems.append(
                    Problem(primitive.path, "capability", f"unknown capability {capability!r}")
                )
            elif fact.supported:
                skipped.append(
                    {"identity": ref, "reason": f"fallback not needed: {harness} has {capability}"}
                )
                del active[ref]
                if fact.evidence != "verified":
                    compiler.gap(
                        ref,
                        capability,
                        "unverified",
                        f"{capability} assumed from {fact.evidence} sources: {fact.note}",
                        remedy=f"if the installed {harness} lacks {capability}, "
                        f"{ref} becomes the provider",
                    )

    # Slots: exactly one provider per declared slot.
    providers: dict[str, list[tuple[str, str]]] = {}
    plugin_hooks = facts.facts["plugin_hooks"].supported
    for ref, layer in active.items():
        primitive = loaded[ref]
        slots = list(primitive.provides)
        if plugin_hooks:
            slots += list(primitive.data.get("hook_provides", []))
        for slot in slots:
            providers.setdefault(slot, []).append((ref, layer))
    for ref in sorted(observed or set()):
        if ref in loaded and ref not in active and ref not in {d["identity"] for d in disables}:
            for slot in loaded[ref].provides:
                providers.setdefault(slot, []).append((ref, "observed (unmanaged)"))
    slot_report: dict[str, Any] = {}
    for slot, (declared, layer) in sorted(merged.slots.items()):
        found = sorted(set(providers.get(slot, [])))
        if len(found) > 1:
            names = " and ".join(f"{ref} (from {source})" for ref, source in found)
            problems.append(
                Problem(
                    f"loadouts/{layer}.yaml.slots.{slot}",
                    "slot-conflict",
                    f"slot {slot!r} has {len(found)} providers: {names}",
                )
            )
        elif declared not in loaded or (
            slot not in loaded[declared].provides
            and slot not in loaded[declared].data.get("hook_provides", [])
        ):
            problems.append(
                Problem(
                    f"loadouts/{layer}.yaml.slots.{slot}",
                    "slot",
                    f"{declared} does not provide {slot!r}",
                )
            )
        elif (
            found and found[0][0] != declared and loaded[found[0][0]].data.get("unless_capability")
        ):
            slot_report[slot] = {
                "provider": found[0][0],
                "declared": declared,
                "layer": found[0][1],
                "status": "fallback",
            }
        elif not found or found[0][0] != declared:
            if profile or declared not in active:
                slot_report[slot] = {
                    "provider": None,
                    "declared": declared,
                    "layer": layer,
                    "status": "unfilled",
                }
                continue
            problems.append(
                Problem(
                    f"loadouts/{layer}.yaml.slots.{slot}",
                    "slot",
                    f"{declared} does not fill {slot!r} on {harness}",
                )
            )
        else:
            slot_report[slot] = {"provider": declared, "layer": layer, "status": "filled"}
    for ref in sorted(active):
        for other in loaded[ref].conflicts:
            if other in active:
                problems.append(
                    Problem(
                        loaded[ref].path,
                        "conflict",
                        f"{ref} conflicts with {other}, which is also enabled",
                    )
                )
    if problems:
        raise ModelError(sorted(set(problems)))

    # Compile.
    for ref, layer in merged.sets["marketplaces"].items():
        if ref in active:
            compiler.marketplace(loaded[ref], layer)
    for ref, layer in merged.sets["plugins"].items():
        if ref in active:
            compiler.plugin(loaded[ref], layer, loaded)
    for server, (entry, layer) in sorted(merged.mcp.items()):
        if entry["primitive"] in active:
            compiler.mcp(server, entry, loaded[entry["primitive"]], layer)
    for ref, layer in merged.sets["hooks"].items():
        if ref in active:
            compiler.hook(loaded[ref], layer)
    for ref, layer in merged.sets["skills"].items():
        if ref in active and not loaded[ref].data.get("external"):
            compiler.skill(loaded[ref], layer)
    for primitive, layer in external_disables:
        compiler.disable(primitive, layer, loaded)
    if facts.facts["project_trust_required"].supported and compiler.effects:
        compiler.gap(
            f"loadout:{name}",
            "project_trust",
            "trust-required",
            facts.facts["project_trust_required"].note + "; Kit never writes trust",
            remedy="trust the project in Codex before expecting these effects",
        )

    text = "\n\n".join(body for _, body in merged.instructions)
    if compiler.soft_rules:
        rules = "\n".join(f"- {rule}" for rule in sorted(set(compiler.soft_rules)))
        text = f"{text}\n\nSoft rules (the harness cannot enforce these):\n{rules}".strip()
    instructions = None
    if text:
        instructions = {
            "target": "CLAUDE.md" if harness == "claude" else "AGENTS.md",
            "marker": f"kit:{name}",
            "layers": [layer for layer, _ in merged.instructions],
            "text": text,
            "owned": True,
        }

    def tool(ref: str, layer: str) -> dict[str, Any]:
        data = loaded[ref].data
        return {
            "identity": ref,
            "layer": layer,
            "check": data.get("check"),
            "install_hint": data.get("install_hint"),
        }

    setup = [
        {**step, "layer": layer, "owned": False}
        for step, layer in merged.setup
        if step["harness"] in ("any", harness)
    ]
    return {
        "format": RESOLVE_FORMAT,
        "loadout": name,
        "profile": profile,
        "harness": {"name": harness, "version": facts.version, "facts_observed": facts.observed},
        "layers": order,
        "primitives": [
            {
                "identity": ref,
                "layer": active[ref],
                "hash": loaded[ref].content_hash,
                "provides": loaded[ref].provides,
            }
            for ref in sorted(active)
        ],
        "skipped": skipped,
        "slots": slot_report,
        "disables": disables,
        "tools": {
            key: [tool(ref, layer) for ref, layer in sorted(merged.tools[key].items())]
            for key in TOOL_FIELDS
        },
        "effects": sorted(compiler.effects, key=lambda e: (e["target"], e["key"])),
        "instructions": instructions,
        "setup": setup,
        "gaps": sorted(compiler.gaps, key=lambda g: (g["subject"], g["capability"])),
        "warnings": [w.as_dict() for w in sorted(warnings)],
    }
