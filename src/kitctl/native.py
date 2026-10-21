"""Read native harness state in explicit roots: plugins, marketplaces, MCP, hooks, blocks.

Claude Code, Codex and Cursor (.cursor/mcp.json and .cursor/hooks.json only).

Read-only. Values that may be secrets are never reported: MCP env and header
values become "reference" or "literal", suspicious arguments are redacted, and
URLs lose credentials and query strings. Trust stores (Codex hooks.state and
projects trust levels) are skipped, never parsed or reported.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from kitctl.model import SECRET_RES

REDACTED = "<redacted>"
SENSITIVE_FLAG = re.compile(r"(?i)^--?[a-z0-9-]*(token|secret|password|passwd|api-?key|auth)")
ENV_REF = re.compile(r"^\$\{(?:env:)?[A-Za-z_][A-Za-z0-9_]*\}$")  # ${NAME}; Cursor ${env:NAME}
BLOCK_RE = re.compile(r"<!--\s*kit:(begin|end)\s+([a-z0-9][a-z0-9._/-]*)\s*-->")


def _safe(path: Path, root: Path) -> bool:
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            return False
    return path.is_file()


def redact_args(args: list[Any]) -> list[str]:
    result: list[str] = []
    hide_next = False
    for raw in args:
        arg = str(raw)
        if hide_next:
            result.append(REDACTED)
            hide_next = False
        elif any(pattern.search(arg) for pattern in SECRET_RES):
            result.append(REDACTED)
        elif SENSITIVE_FLAG.match(arg):
            flag, sep, _ = arg.partition("=")
            result.append(f"{flag}={REDACTED}" if sep else flag)
            hide_next = not sep
        else:
            result.append(redact_url(arg) if "://" in arg else arg)
    return result


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return REDACTED
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _value_kind(value: Any) -> str:
    return "reference" if isinstance(value, str) and ENV_REF.match(value) else "literal"


class Reader:
    def __init__(self, root: Path, scope_home: bool) -> None:
        self.root = root
        self.home = scope_home
        self.plugins: list[dict[str, Any]] = []
        self.skills: list[dict[str, Any]] = []
        self.marketplaces: list[dict[str, Any]] = []
        self.mcp: list[dict[str, Any]] = []
        self.hooks: list[dict[str, Any]] = []
        self.installed: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def _load(self, relative: str, fmt: str) -> Any:
        path = self.root / relative
        if not path.exists() or not _safe(path, self.root):
            return None  # Missing or linked; inventory's file pass already warns about links.
        try:
            text = path.read_text(encoding="utf-8")
            if fmt == "toml":
                return tomllib.loads(text)
            return json.loads(text) if text.strip() else {}
        except (OSError, UnicodeDecodeError, ValueError) as error:
            kind = type(error).__name__
            self.warnings.append(f"Cannot parse {path} ({kind}); skipped")
            return None

    def _where(self, relative: str) -> str:
        return str(self.root / relative)

    def claude_settings(self, relative: str, scope: str) -> None:
        data = self._load(relative, "json")
        if not isinstance(data, dict):
            return
        enabled = data.get("enabledPlugins", {})
        if isinstance(enabled, dict):
            for plugin_id, value in sorted(enabled.items()):
                self.plugins.append(
                    {
                        "harness": "claude",
                        "scope": scope,
                        "file": self._where(relative),
                        "id": plugin_id,
                        "enabled": value is True,
                    }
                )
        markets = data.get("extraKnownMarketplaces", {})
        if isinstance(markets, dict):
            for name, spec in sorted(markets.items()):
                self.marketplaces.append(
                    {
                        "harness": "claude",
                        "scope": scope,
                        "file": self._where(relative),
                        "name": name,
                        "source": _market_source(
                            spec.get("source") if isinstance(spec, dict) else None
                        ),
                    }
                )
        overrides = data.get("skillOverrides", {})
        if isinstance(overrides, dict):
            for skill, value in sorted(overrides.items()):
                self.skills.append(
                    {
                        "harness": "claude",
                        "scope": scope,
                        "file": self._where(relative),
                        "skill": skill,
                        "enabled": value != "off",
                    }
                )
        hooks = data.get("hooks", {})
        self._hooks(hooks, "claude", scope, relative)

    def _hooks(self, hooks: Any, harness: str, scope: str, relative: str) -> None:
        if not isinstance(hooks, dict):
            return
        for event, groups in sorted(hooks.items()):
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []) or []:
                    if not isinstance(hook, dict):
                        continue
                    command = hook.get("command", "")
                    self.hooks.append(
                        {
                            "harness": harness,
                            "scope": scope,
                            "file": self._where(relative),
                            "event": event,
                            "matcher": group.get("matcher", ""),
                            "command": " ".join(redact_args(str(command).split())),
                        }
                    )

    def _servers(
        self, servers: Any, harness: str, scope: str, relative: str, project: str | None = None
    ) -> None:
        if not isinstance(servers, dict):
            return
        for server_id, spec in sorted(servers.items()):
            if not isinstance(spec, dict):
                continue
            env = spec.get("env", {}) if isinstance(spec.get("env"), dict) else {}
            headers = spec.get("headers", spec.get("http_headers", {}))
            headers = headers if isinstance(headers, dict) else {}
            entry: dict[str, Any] = {
                "harness": harness,
                "scope": scope,
                "file": self._where(relative),
                "id": server_id,
                "transport": spec.get("type", "http" if "url" in spec else "stdio"),
                "command": spec.get("command"),
                "args": redact_args(
                    spec.get("args", []) if isinstance(spec.get("args"), list) else []
                ),
                "env": {name: _value_kind(value) for name, value in sorted(env.items())},
                "headers": {name: _value_kind(value) for name, value in sorted(headers.items())},
            }
            if isinstance(spec.get("url"), str):
                entry["url"] = redact_url(spec["url"])
            if harness == "codex":
                entry["env_vars"] = [
                    item if isinstance(item, str) else str(item.get("name", "?"))
                    for item in spec.get("env_vars", [])
                    if isinstance(item, (str, dict))
                ]
                entry["enabled"] = spec.get("enabled", True) is not False
                for key in ("enabled_tools", "disabled_tools"):
                    if isinstance(spec.get(key), list):
                        entry[key] = [str(tool) for tool in spec[key]]
            if project is not None:
                entry["project"] = project
            self.mcp.append(entry)

    def claude_mcp_file(self) -> None:
        data = self._load(".mcp.json", "json")
        if isinstance(data, dict):
            self._servers(data.get("mcpServers"), "claude", "project", ".mcp.json")

    def claude_user_state(self, relative: str) -> None:
        data = self._load(relative, "json")
        if not isinstance(data, dict):
            return
        self._servers(data.get("mcpServers"), "claude", "user", relative)
        projects = data.get("projects", {})
        if isinstance(projects, dict):
            for project, spec in sorted(projects.items()):
                if isinstance(spec, dict):
                    self._servers(spec.get("mcpServers"), "claude", "local", relative, project)

    def claude_installed(self, relative: str) -> None:
        data = self._load(relative, "json")
        if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
            return
        for plugin_id, installs in sorted(data["plugins"].items()):
            for install in installs if isinstance(installs, list) else []:
                if isinstance(install, dict):
                    self.installed.append(
                        {
                            "harness": "claude",
                            "file": self._where(relative),
                            "id": plugin_id,
                            "scope": install.get("scope"),
                            "project": install.get("projectPath"),
                            "version": install.get("version"),
                            "sha": install.get("gitCommitSha"),
                            "path": install.get("installPath"),
                        }
                    )

    def codex_config(self, relative: str, scope: str) -> None:
        data = self._load(relative, "toml")
        if not isinstance(data, dict):
            return
        self._servers(data.get("mcp_servers"), "codex", scope, relative)
        plugins = data.get("plugins", {})
        if isinstance(plugins, dict):
            for plugin_id, spec in sorted(plugins.items()):
                if isinstance(spec, dict) and "enabled" in spec:
                    self.plugins.append(
                        {
                            "harness": "codex",
                            "scope": scope,
                            "file": self._where(relative),
                            "id": plugin_id,
                            "enabled": spec["enabled"] is True,
                        }
                    )
        markets = data.get("marketplaces", {})
        if isinstance(markets, dict):
            for name, spec in sorted(markets.items()):
                source = spec.get("source") if isinstance(spec, dict) else None
                self.marketplaces.append(
                    {
                        "harness": "codex",
                        "scope": scope,
                        "file": self._where(relative),
                        "name": name,
                        "source": {
                            "kind": spec.get("source_type") if isinstance(spec, dict) else None,
                            "location": redact_url(source)
                            if isinstance(source, str) and "://" in source
                            else source,
                            "ref": spec.get("ref") if isinstance(spec, dict) else None,
                        },
                    }
                )
        skills = data.get("skills", {})
        if isinstance(skills, dict) and scope == "user":
            for item in skills.get("config", []) if isinstance(skills.get("config"), list) else []:
                if isinstance(item, dict) and "enabled" in item:
                    self.skills.append(
                        {
                            "harness": "codex",
                            "scope": scope,
                            "file": self._where(relative),
                            "skill": item.get("name") or item.get("path"),
                            "enabled": item["enabled"] is True,
                        }
                    )
        hooks = data.get("hooks", {})
        if isinstance(hooks, dict):
            # hooks.state holds trust approvals: never read it.
            self._hooks({k: v for k, v in hooks.items() if k != "state"}, "codex", scope, relative)

    def codex_hooks(self, relative: str, scope: str) -> None:
        data = self._load(relative, "json")
        if isinstance(data, dict):
            self._hooks(data.get("hooks"), "codex", scope, relative)

    def cursor_mcp(self, relative: str, scope: str) -> None:
        data = self._load(relative, "json")
        if isinstance(data, dict):
            self._servers(data.get("mcpServers"), "cursor", scope, relative)

    def cursor_hooks(self, relative: str, scope: str) -> None:
        """{"version": 1, "hooks": {"<event>": [{"command", "matcher"?, ...}]}}: flat entries."""
        data = self._load(relative, "json")
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            return
        for event, items in sorted(hooks.items()):
            for hook in items if isinstance(items, list) else []:
                if isinstance(hook, dict):
                    self.hooks.append(
                        {
                            "harness": "cursor",
                            "scope": scope,
                            "file": self._where(relative),
                            "event": event,
                            "matcher": hook.get("matcher", ""),
                            "command": " ".join(redact_args(str(hook.get("command", "")).split())),
                        }
                    )

    def managed_blocks(self, relative: str) -> None:
        path = self.root / relative
        if not path.exists() or not _safe(path, self.root):
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.warnings.append(f"Cannot read {path}")
            return
        open_blocks: dict[str, int] = {}
        counts: dict[str, int] = {}
        problems: dict[str, str] = {}
        for match in BLOCK_RE.finditer(text):
            kind, name = match.groups()
            line = text.count("\n", 0, match.start()) + 1
            if kind == "begin":
                counts[name] = counts.get(name, 0) + 1
                if name in open_blocks:
                    problems.setdefault(name, f"nested begin at line {line}")
                open_blocks[name] = line
            elif name not in open_blocks:
                problems.setdefault(name, f"end without begin at line {line}")
                counts.setdefault(name, 0)
            else:
                del open_blocks[name]
        for name, line in open_blocks.items():
            problems.setdefault(name, f"begin at line {line} is never closed")
        for name, count in counts.items():
            if count > 1:
                problems.setdefault(name, f"{count} blocks with the same marker")
        for name in sorted(counts):
            self.blocks.append(
                {
                    "file": str(path),
                    "name": name,
                    "status": "duplicate"
                    if counts[name] > 1
                    else ("malformed" if name in problems else "ok"),
                    **({"problem": problems[name]} if name in problems else {}),
                }
            )


def _market_source(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {"kind": None}
    kind = source.get("source")
    location = source.get("url") or source.get("repo") or source.get("path")
    if isinstance(location, str) and "://" in location:
        location = redact_url(location)
    return {"kind": kind, "location": location, "ref": source.get("ref")}


def read_native(root: Path, home: Path) -> dict[str, Any]:
    """Native state under one explicit root. The home root reads user-scope files."""
    is_home = root == home.expanduser().resolve()
    reader = Reader(root, is_home)
    reader.claude_settings(".claude/settings.json", "user" if is_home else "project")
    if not is_home:
        reader.claude_settings(".claude/settings.local.json", "local")
        reader.claude_mcp_file()
        reader.codex_config(".codex/config.toml", "project")
        reader.codex_hooks(".codex/hooks.json", "project")
        reader.cursor_mcp(".cursor/mcp.json", "project")
        reader.cursor_hooks(".cursor/hooks.json", "project")
    else:
        # ~/.claude.json, or inside the config dir when CLAUDE_CONFIG_DIR=~/.claude.
        reader.claude_user_state(".claude.json")
        reader.claude_user_state(".claude/.claude.json")
        reader.claude_installed(".claude/plugins/installed_plugins.json")
        reader.codex_config(".codex/config.toml", "user")
        reader.codex_hooks(".codex/hooks.json", "user")
        reader.cursor_mcp(".cursor/mcp.json", "user")
        reader.cursor_hooks(".cursor/hooks.json", "user")
    for name in ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md"):
        reader.managed_blocks(name)
    return {
        "plugins": reader.plugins,
        "skill_switches": reader.skills,
        "marketplaces": reader.marketplaces,
        "mcp_servers": reader.mcp,
        "hooks": reader.hooks,
        "installed_plugins": reader.installed,
        "managed_blocks": reader.blocks,
        "warnings": reader.warnings,
    }
