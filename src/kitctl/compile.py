"""Compile desired state into effects: precise insertions into native harness config.

An :class:`Effect` names one thing kitctl will own: a JSON key or array element, a TOML
table, a copied file or skill folder, or a marked block in a Markdown file. Compilation is
pure; :mod:`kitctl.engine` turns effects into a diff and applies them.

Anything a harness can't represent becomes a :class:`Gap` and fails the plan, with a
message saying what and why (for example a strict MCP tool allowlist for Claude Code,
whose permission rules can deny tools but can't hide them from a server's tool list).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kitctl.desired import Desired, DesiredError, Item, Primitive, server_config
from kitctl.hashing import is_junk
from kitctl.paths import Paths
from kitctl.safemeta import canonical_hash
from kitctl.secretscan import find

MARKETPLACE = "kitctl"
# ${NAME} or ${NAME:-default}, as Claude Code expands them in MCP config.
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}")
_BEARER = re.compile(r"^Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class Effect:
    owner: str
    item: str  # "skill:writer", "mcp:github", "capture:guard", ...
    harness: str
    op: str  # json-key | json-element | toml-key | file | tree | block
    target: Path
    identity: tuple[str, ...]
    value: Any = field(compare=False, hash=False)
    source: Path | None = field(default=None, compare=False, hash=False)

    @property
    def key(self) -> str:
        return f"{self.target}\n{self.op}\n{json.dumps(list(self.identity))}"


@dataclass(frozen=True)
class Gap:
    item: str
    harness: str
    message: str


@dataclass
class Compiled:
    effects: list[Effect]
    gaps: list[Gap]
    notes: list[str]


def element_id(value: Any) -> str:
    return "#" + canonical_hash(value)[:16]


def raw_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(directory: Path) -> dict[str, Path]:
    """Files under ``directory`` by POSIX relative path, skipping junk and links.

    ``directory`` itself must not be a link: a linked ``files/`` could pull anything on the
    machine (keys, other projects) into agent config or a committed marketplace.
    """
    found: dict[str, Path] = {}
    if directory.is_symlink():
        raise ValueError(f"{directory} is a symlink; kitctl copies only real directories")
    if not directory.is_dir():
        return found
    stack = [directory]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir()):
            if child.is_symlink() or is_junk(child.name, child.is_dir()):
                continue
            if child.is_dir():
                stack.append(child)
            elif child.is_file():
                found[child.relative_to(directory).as_posix()] = child
    return dict(sorted(found.items()))


# -- MCP translation ----------------------------------------------------------------------

_SENSITIVE = re.compile(r"(token|key|secret|password|passwd|auth|credential|bearer|cookie)", re.I)


def env_references(items: list[Item]) -> dict[str, list[str]]:
    """Environment variables the desired MCP servers reference: {NAME: [item refs]}."""
    found: dict[str, list[str]] = {}
    for item in items:
        if item.kind != "mcp" or item.primitive is None:
            continue
        try:
            text = json.dumps(server_config(item.primitive))
        except (OSError, ValueError, DesiredError):
            continue
        names = {m.group(1) for m in _REF.finditer(text)}
        config = json.loads(text)
        names.update(n for n in config.get("env_vars") or [] if isinstance(n, str))
        if isinstance(config.get("bearer_token_env_var"), str):
            names.add(config["bearer_token_env_var"])
        names.update(v for v in (config.get("env_http_headers") or {}).values())
        for name in names:
            found.setdefault(name, []).append(item.ref)
    return dict(sorted(found.items()))


def env_readiness(items: list[Item], env: dict[str, str] | None = None) -> list[str]:
    """Unset referenced variables, as human-readable lines (names only, never values)."""
    import os

    env = dict(os.environ) if env is None else env
    return [
        f"{name} is not set in this environment (used by {', '.join(refs)})"
        for name, refs in env_references(items).items()
        if not env.get(name)
    ]


def credential_problems(config: dict[str, Any]) -> list[str]:
    """Literal credentials in a server definition (kitctl writes only ${NAME} references)."""
    problems = []
    found = find(json.dumps(config))
    if found:
        problems.append(f"contains what looks like a literal credential ({', '.join(found)})")
    for table in ("env", "headers", "http_headers"):
        for name, value in (config.get(table) or {}).items():
            if isinstance(value, str) and "${" not in value and _SENSITIVE.search(name):
                problems.append(f"{table}.{name} holds a literal value; use ${{{name.upper()}}}")
    args = [str(a) for a in config.get("args") or []]
    for flag, value in zip(args, args[1:], strict=False):
        if flag.startswith("-") and _SENSITIVE.search(flag) and "${" not in value:
            problems.append(f"the value after {flag} is literal; use an environment reference")
    return problems


def _neutral(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Claude- or Codex-shaped server definition to one neutral form."""
    if "url" in config:
        headers = dict(config.get("headers") or config.get("http_headers") or {})
        if config.get("bearer_token_env_var"):
            headers["Authorization"] = f"Bearer ${{{config['bearer_token_env_var']}}}"
        for header, variable in (config.get("env_http_headers") or {}).items():
            headers[header] = f"${{{variable}}}"
        kind = "sse" if config.get("type") == "sse" else "http"
        server: dict[str, Any] = {"type": kind, "url": config["url"]}
        if headers:
            server["headers"] = headers
    else:
        env = dict(config.get("env") or {})
        for variable in config.get("env_vars") or []:
            name = variable if isinstance(variable, str) else variable.get("name")
            if isinstance(name, str):
                env.setdefault(name, f"${{{name}}}")
        server = {"type": "stdio", "command": config.get("command", "")}
        if config.get("args"):
            server["args"] = list(config["args"])
        if env:
            server["env"] = env
    for key in ("enabled_tools", "disabled_tools"):
        if key in config:
            server[key] = list(config[key])
    return server


def _filters(
    item: Item, neutral: dict[str, Any], overrides: dict[str, list[str]]
) -> dict[str, list[str]]:
    merged = {k: neutral[k] for k in ("enabled_tools", "disabled_tools") if k in neutral}
    for key, value in overrides.items():
        if key in merged and sorted(merged[key]) != sorted(value):
            raise DesiredError(
                f"{item.ref}: kit.toml {key} conflicts with the server's own {key} in "
                "server.json; keep one"
            )
        merged[key] = value
    return merged


def claude_server(neutral: dict[str, Any]) -> dict[str, Any]:
    keys = (
        ("type", "url", "headers")
        if neutral["type"] != "stdio"
        else (
            "type",
            "command",
            "args",
            "env",
        )
    )
    return {k: neutral[k] for k in keys if k in neutral}


def codex_server(item: str, neutral: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Codex config.toml table, or reasons it can't be expressed."""
    problems: list[str] = []
    table: dict[str, Any] = {}
    if neutral["type"] == "stdio":
        for key in ("command", "args"):
            values = neutral.get(key)
            if values is None:
                continue
            texts = values if isinstance(values, list) else [values]
            if any(_REF.search(str(v)) for v in texts):
                problems.append(f"Codex doesn't expand ${{VAR}} in {key}")
            table[key] = values
        literal: dict[str, str] = {}
        forwarded: list[str] = []
        for name, value in (neutral.get("env") or {}).items():
            match = _REF.fullmatch(str(value))
            if match and match.group(1) == name and not match.group(2):
                forwarded.append(name)
            elif match or _REF.search(str(value)):
                problems.append(
                    f"env {name}={value} can't be expressed in Codex (it forwards variables by "
                    "their own name only, without defaults)"
                )
            else:
                literal[name] = str(value)
        if literal:
            table["env"] = literal
        if forwarded:
            table["env_vars"] = forwarded
    else:
        if neutral["type"] == "sse":
            problems.append("Codex supports streamable HTTP servers, not SSE")
        if _REF.search(neutral["url"]):
            problems.append("Codex doesn't expand ${VAR} in url")
        table["url"] = neutral["url"]
        plain: dict[str, str] = {}
        from_env: dict[str, str] = {}
        for header, value in (neutral.get("headers") or {}).items():
            bearer = _BEARER.match(str(value))
            match = _REF.fullmatch(str(value))
            if header.lower() == "authorization" and bearer:
                table["bearer_token_env_var"] = bearer.group(1)
            elif match and not match.group(2):
                from_env[header] = match.group(1)
            elif _REF.search(str(value)):
                problems.append(f"header {header} mixes text and ${{VAR}}; Codex can't do that")
            else:
                plain[header] = str(value)
        if plain:
            table["http_headers"] = plain
        if from_env:
            table["env_http_headers"] = from_env
    for key in ("enabled_tools", "disabled_tools"):
        if key in neutral:
            table[key] = neutral[key]
    return table, [f"{item}: {p}" for p in problems]


# -- compiler -----------------------------------------------------------------------------


class _Compiler:
    def __init__(self, desired: Desired, paths: Paths, warehouse: Path) -> None:
        self.desired = desired
        self.paths = paths
        self.warehouse = warehouse
        self.effects: list[Effect] = []
        self.gaps: list[Gap] = []
        self.notes: list[str] = list(desired.notes)
        self.repo = desired.repo
        self.project = desired.scope == "project"

    def add(
        self,
        item: str,
        harness: str,
        op: str,
        target: Path,
        identity: tuple[str, ...],
        value: Any,
        source: Path | None = None,
    ) -> None:
        self.effects.append(
            Effect(self.desired.owner, item, harness, op, target, identity, value, source)
        )

    def gap(self, item: Item | str, harness: str, message: str) -> None:
        ref = item if isinstance(item, str) else item.ref
        self.gaps.append(Gap(ref, harness, message))

    # Locations ---------------------------------------------------------------------------

    def claude_dir(self) -> Path:
        return self.repo / ".claude" if self.repo else self.paths.claude_config_dir

    def claude_settings(self) -> Path:
        return self.claude_dir() / "settings.json"

    def codex_dir(self) -> Path:
        return self.repo / ".codex" if self.repo else self.paths.codex_home

    # Kinds -------------------------------------------------------------------------------

    def tree(self, item: Item, harness: str, source: Path, target: Path) -> None:
        files = source_files(source)
        if not files:
            self.gap(item, harness, f"{source} has no files to install")
            return
        digests = {rel: raw_digest(path) for rel, path in files.items()}
        self.add(item.ref, harness, "tree", target, (), digests, source)

    def files(
        self,
        item: Item,
        harness: str,
        primitive: Primitive,
        target_dir: Path,
        suffixes: tuple[str, ...],
    ) -> None:
        files = source_files(primitive.directory / "files")
        chosen = {rel: path for rel, path in files.items() if rel.endswith(suffixes)}
        if not chosen or "/" in "".join(chosen):
            self.gap(
                item,
                harness,
                f"expected top-level {'/'.join(suffixes)} files in {primitive.directory / 'files'}",
            )
            return
        for rel, path in chosen.items():
            self.add(item.ref, harness, "file", target_dir / rel, (), raw_digest(path), path)

    def skill(self, item: Item, harness: str, primitive: Primitive) -> None:
        if harness == "claude":
            target = self.claude_dir() / "skills" / item.name
        else:
            base = self.repo / ".agents" if self.repo else self.paths.agents_home
            target = base / "skills" / item.name
        self.tree(item, harness, primitive.directory / "files", target)

    def markdown_kind(self, item: Item, harness: str, primitive: Primitive, folder: str) -> None:
        if harness == "codex":
            self.gap(
                item,
                harness,
                f"Codex has no file-based {folder}; set harnesses = "
                '["claude"] in its primitive.toml',
            )
            return
        self.files(item, harness, primitive, self.claude_dir() / folder, (".md",))

    def rules(self, item: Item, harness: str, primitive: Primitive) -> None:
        files = source_files(primitive.directory / "files")
        markdown = {r: p for r, p in files.items() if r.endswith(".md")}
        policy = {r: p for r, p in files.items() if r.endswith(".rules")}
        if harness == "claude":
            if not markdown:
                self.gap(item, harness, "Claude Code rules are Markdown; this primitive has none")
                return
            self.files(item, harness, primitive, self.claude_dir() / "rules", (".md",))
            return
        if policy:
            if self.project:
                self.gap(item, harness, "Codex .rules files load from the user rules/ only")
            else:
                self.files(item, harness, primitive, self.paths.codex_home / "rules", (".rules",))
        if markdown:
            text = "\n\n".join(p.read_text("utf-8").strip() for p in markdown.values())
            target = (self.repo or self.paths.codex_home) / "AGENTS.md"
            self.add(item.ref, harness, "block", target, (item.ref,), text + "\n")
        if not policy and not markdown:
            self.gap(item, harness, "no .md or .rules files to install")

    def mcp(self, item: Item, harness: str, primitive: Primitive) -> None:
        config = server_config(primitive)
        problems = credential_problems(config)
        if problems:
            for problem in problems:
                self.gap(item, harness, f"server.json {problem}; kitctl won't write it")
            return
        neutral = _neutral(config)
        filters = _filters(item, neutral, self.desired.filters.get(item.name, {}))
        neutral = {k: v for k, v in neutral.items() if k not in filters} | filters
        if harness == "claude":
            if "enabled_tools" in filters:
                self.gap(
                    item,
                    harness,
                    "a strict tool allowlist (enabled_tools) can't be enforced in Claude Code: "
                    "permission rules deny tools but don't remove them from the server's tool "
                    "list. Use disabled_tools, or restrict this repository to Codex.",
                )
                return
            target = self.repo / ".mcp.json" if self.repo else self.paths.claude_json
            identity = ("mcpServers", item.name)
            self.add(item.ref, harness, "json-key", target, identity, claude_server(neutral))
            for tool in filters.get("disabled_tools", []):
                rule = f"mcp__{item.name}__{tool}"
                self.add(
                    item.ref,
                    harness,
                    "json-element",
                    self.claude_settings(),
                    ("permissions", "deny", element_id(rule)),
                    rule,
                )
            return
        table, problems = codex_server(item.ref, neutral)
        for problem in problems:
            self.gap(item, harness, problem)
        if problems:
            return
        target = self.codex_dir() / "config.toml"
        self.add(item.ref, harness, "toml-key", target, ("mcp_servers", item.name), table)
        if self.project:
            self.notes.append(
                f"{item.ref}: Codex reads {target} only in trusted projects; kitctl never "
                "changes trust"
            )

    def hook(self, item: Item, harness: str, primitive: Primitive) -> None:
        spec = primitive.meta.get("hook")
        if not isinstance(spec, dict):
            self.gap(item, harness, "hook primitive has no [hook] table (event, script/command)")
            return
        base = (self.claude_dir() if harness == "claude" else self.codex_dir()) / "hooks"
        installed = base / "kitctl" / item.name
        command = spec.get("command", "{script}")
        script = spec.get("script")
        if script and harness == "codex" and self.project:
            self.gap(
                item,
                harness,
                "a Codex project hook would need this machine's absolute "
                "script path in a committed file; put the hook in core (user scope)",
            )
            return
        if script:
            self.tree(item, harness, primitive.directory / "files", installed)
            if harness == "claude" and self.project:
                where = f'"$CLAUDE_PROJECT_DIR"/.claude/hooks/kitctl/{item.name}/{script}'
            else:
                where = _quote((installed / script).as_posix())
            command = command.replace("{script}", where)
        elif "{script}" in command:
            self.gap(item, harness, "hook command uses {script} but no script is set")
            return
        handler: dict[str, Any] = {"type": "command", "command": command}
        if "timeout" in spec:
            handler["timeout"] = spec["timeout"]
        group: dict[str, Any] = {"hooks": [handler]}
        if spec.get("matcher"):
            group = {"matcher": spec["matcher"], **group}
        target = self.claude_settings() if harness == "claude" else self.codex_dir() / "hooks.json"
        identity = ("hooks", spec["event"], element_id(group))
        self.add(item.ref, harness, "json-element", target, identity, group)
        if harness == "codex":
            self.notes.append(
                f"{item.ref}: Codex asks you to review new or changed hooks before running "
                "them; kitctl never marks hooks trusted"
            )

    def plugin(self, item: Item, harness: str) -> None:
        if harness == "codex":
            self.gap(
                item,
                harness,
                "Codex plugin enablement isn't compiled; use "
                '`codex plugin install`, or set harnesses = ["claude"]',
            )
            return
        plugin_id = item.name
        if item.primitive is not None:
            if self.project:
                self.gap(
                    item,
                    harness,
                    "warehouse plugins are served from a local marketplace "
                    "path, so they can be enabled at user scope only",
                )
                return
            listed = self.warehouse / "marketplace" / ".claude-plugin" / "marketplace.json"
            try:
                names = {p.get("name") for p in json.loads(listed.read_text("utf-8"))["plugins"]}
            except (OSError, ValueError, KeyError, TypeError):
                names = set()
            if item.name not in names:
                self.gap(
                    item,
                    harness,
                    "not in the warehouse marketplace; run `kitctl marketplace build` first",
                )
                return
            plugin_id = f"{item.name}@{MARKETPLACE}"
            source = {"source": {"source": "directory", "path": str(listed.parent.parent)}}
            self.add(
                f"marketplace:{MARKETPLACE}",
                harness,
                "json-key",
                self.claude_settings(),
                ("extraKnownMarketplaces", MARKETPLACE),
                source,
            )
        self.add(
            item.ref,
            harness,
            "json-key",
            self.claude_settings(),
            ("enabledPlugins", plugin_id),
            True,
        )

    def run(self) -> Compiled:
        for item in self.desired.items:
            primitive = item.primitive
            harnesses = primitive.harnesses if primitive else ("claude",)
            for harness in self.desired.harnesses:
                if harness not in harnesses:
                    continue
                try:
                    if item.kind == "plugin":
                        self.plugin(item, harness)
                    elif primitive is None:
                        continue
                    elif item.kind == "skill":
                        self.skill(item, harness, primitive)
                    elif item.kind in ("agent", "command"):
                        self.markdown_kind(item, harness, primitive, f"{item.kind}s")
                    elif item.kind == "rules":
                        self.rules(item, harness, primitive)
                    elif item.kind == "mcp":
                        self.mcp(item, harness, primitive)
                    elif item.kind == "hook":
                        self.hook(item, harness, primitive)
                except (OSError, ValueError) as error:
                    self.gap(item, harness, str(error))
        unique: dict[str, Effect] = {}
        for effect in self.effects:
            previous = unique.get(effect.key)
            if previous is not None and previous.value != effect.value:
                self.gap(
                    effect.item,
                    effect.harness,
                    f"conflicts with {previous.item} at {effect.target}",
                )
            unique.setdefault(effect.key, effect)
        return Compiled(list(unique.values()), self.gaps, self.notes)


def _quote(path: str) -> str:
    return f'"{path}"' if re.search(r"[\s'\"$`\\]", path) else path


def compile_desired(desired: Desired, paths: Paths, warehouse: Path) -> Compiled:
    return _Compiler(desired, paths, warehouse).run()
