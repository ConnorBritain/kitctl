"""Codex source adapter (best effort).

Config, trust, MCP keys, skills (including ``skills/.system``), rules, and the plugin cache
layout (``plugins/cache/<marketplace>/<plugin>/<version>/.codex-plugin/plugin.json``) were
verified against a real Codex 0.153.4 install on macOS (2026-10-12). Plugin enabled state
is read from ``[plugins."<id>"]`` tables when present (unconfirmed; see docs/followups.md).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kitctl.collect import Collector
from kitctl.hashing import HASH_SCHEME
from kitctl.model import Entry
from kitctl.paths import Paths
from kitctl.safemeta import iter_hooks
from kitctl.sources.claude import mcp_servers

HARNESS = "codex"
VERIFIED_VERSIONS: tuple[str, ...] = ("0.153.4",)

INSTRUCTIONS = ("AGENTS.override.md", "AGENTS.md")


def _config(c: Collector, path: Path, scope: str, name: str, link: Any, repo: Path | None) -> Any:
    document = c.read_toml(path)
    meta: dict[str, Any] = {"invalid": True}
    if document is not None:
        meta = {"keys": sorted(document)}
        projects = document.get("projects")
        if isinstance(projects, Mapping):
            meta["trusted_projects"] = sorted(
                str(project)
                for project, state in projects.items()
                if isinstance(state, Mapping) and state.get("trust_level") == "trusted"
            )
    c.file_entry("settings", HARNESS, scope, name, path, link, repo, metadata=meta)
    if document is not None:
        mcp_servers(c, document.get("mcp_servers"), path, scope, repo, harness=HARNESS)
        if isinstance(document.get("hooks"), Mapping):
            _hooks(c, document["hooks"], path, scope, repo)
    return document


def _hooks(c: Collector, hooks: Any, source: Path, scope: str, repo: Path | None) -> None:
    for meta in iter_hooks(hooks):
        name = f"{meta['event']}:{meta['matcher']}:{meta.get('command') or meta['type']}"
        c.add(
            Entry(
                "hook",
                HARNESS,
                scope,
                name,
                str(source),
                meta.get("command_sha256"),
                None,
                repo=str(repo) if repo else None,
                metadata={**meta, "source": "config"},
            )
        )


def _hooks_file(c: Collector, base: Path, scope: str, repo: Path | None, prefix: str) -> None:
    path, link = c.reach(base, "hooks.json", HARNESS, scope, repo)
    if path is None:
        return
    document = c.read_json(path)
    c.file_entry(
        "settings",
        HARNESS,
        scope,
        f"{prefix}hooks.json",
        path,
        link,
        repo,
        metadata={"hook_events": sorted(_events(document))},
    )
    _hooks(c, document, path, scope, repo)


def _events(document: Any) -> list[str]:
    if isinstance(document, Mapping) and isinstance(document.get("hooks"), Mapping):
        document = document["hooks"]
    return [str(key) for key in document] if isinstance(document, Mapping) else []


def _instructions(
    c: Collector, base: Path, relative: str, scope: str, repo: Path | None, harness: str = HARNESS
) -> None:
    path, link = c.reach(base, relative, harness, scope, repo)
    if path is not None:
        c.file_entry("instructions", harness, scope, relative, path, link, repo)


def _skills(
    c: Collector,
    base: Path,
    relative: str,
    scope: str,
    repo: Path | None,
    extra: dict[str, Any] | None = None,
) -> None:
    path, _ = c.reach(base, relative, HARNESS, scope, repo)
    if path is not None and path.is_dir():
        for leftover in c.skills_dir(path, HARNESS, scope, repo, None, extra):
            if leftover.name == ".system":  # bundled system skills (Codex 0.153.4)
                list(c.skills_dir(leftover, HARNESS, scope, repo, "codex:system", extra))


def _manifest(root: Path) -> Path | None:
    for candidate in (root / ".codex-plugin" / "plugin.json", root / "plugin.json"):
        if candidate.is_file():
            return candidate
    return None


def _version_key(path: Path) -> tuple[tuple[int, ...], int]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", path.name))
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return numbers, mtime


def _relative_paths(root: Path, value: Any) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    found = []
    for item in values:
        if isinstance(item, str) and not os.path.isabs(item) and ".." not in Path(item).parts:
            found.append(root / item)
    return found


def _plugin(c: Collector, root: Path, pid: str, enabled: bool | None, versions: int) -> None:
    manifest = _manifest(root)
    document = c.read_json(manifest) if manifest else None
    document = document if isinstance(document, Mapping) else {}
    meta: dict[str, Any] = {
        "version": document.get("version") or root.name,
        "description": document.get("description"),
        "cached_versions": versions,
    }
    if enabled is not None:
        meta["enabled"] = enabled
    c.add(
        Entry(
            "plugin",
            HARNESS,
            "user",
            pid,
            str(root),
            c.digest(manifest) if manifest else None,
            HASH_SCHEME if manifest else None,
            metadata={k: v for k, v in meta.items() if v is not None},
        )
    )
    skill_dirs = [root / "skills", *_relative_paths(root, document.get("skills"))]
    for directory in dict.fromkeys(skill_dirs):
        if directory.is_dir():
            list(c.skills_dir(directory, HARNESS, "plugin", None, pid))
    declared = document.get("mcpServers")
    sources = [root / ".mcp.json", root / "mcp.json"]
    if isinstance(declared, (str, list)):
        sources += _relative_paths(root, declared)
    elif isinstance(declared, Mapping):
        mcp_servers(c, declared, manifest or root, "plugin", None, pid, harness=HARNESS)
    for source in dict.fromkeys(sources):
        if source.is_file():
            servers = c.read_json(source)
            if isinstance(servers, Mapping) and isinstance(servers.get("mcpServers"), Mapping):
                servers = servers["mcpServers"]
            mcp_servers(c, servers, source, "plugin", None, pid, harness=HARNESS)
    hooks = root / "hooks" / "hooks.json"
    if hooks.is_file():
        _hooks(c, c.read_json(hooks), hooks, "plugin", None)


def _plugins(c: Collector, directory: Path, enabled: Mapping[str, bool]) -> None:
    """Installed plugins: ``cache/<marketplace>/<plugin>/<version>/`` (newest version wins)."""
    cache = directory / "cache"
    if cache.is_dir():
        for marketplace in c.children(cache):
            if not marketplace.is_dir() or marketplace.name.startswith("."):
                continue
            for plugin in c.children(marketplace):
                if not plugin.is_dir() or plugin.name.startswith("."):
                    continue
                versions = [v for v in c.children(plugin) if v.is_dir() and _manifest(v)]
                if not versions and _manifest(plugin):
                    versions = [plugin]
                if not versions:
                    continue
                newest = max(versions, key=_version_key)
                pid = f"{plugin.name}@{marketplace.name}"
                _plugin(c, newest, pid, enabled.get(pid), len(versions))
    for root in c.children(directory):  # plugins installed directly (portable layout)
        if root.name != "cache" and root.is_dir() and _manifest(root):
            _plugin(c, root, f"{root.name}@local", enabled.get(f"{root.name}@local"), 1)


def _enabled_plugins(document: Any) -> dict[str, bool]:
    plugins = document.get("plugins") if isinstance(document, Mapping) else None
    if not isinstance(plugins, Mapping):
        return {}
    return {
        str(pid): bool(state.get("enabled", True))
        for pid, state in plugins.items()
        if isinstance(state, Mapping)
    }


def collect_user(c: Collector, paths: Paths) -> set[str]:
    """Collect user and managed Codex config; return trusted project paths."""
    trusted: set[str] = set()
    for directory in paths.codex_managed:
        if not directory.is_dir():
            continue
        for managed in sorted(directory.glob("*.toml")):
            _config(c, managed, "managed", managed.name, None, None)
        _skills(c, directory, "skills", "managed", None)
    base = paths.codex_home
    if base.is_dir():
        path, link = c.reach(base, "config.toml", HARNESS, "user")
        document = None
        if path is not None:
            document = _config(c, path, "user", "config.toml", link, None)
            projects = document.get("projects") if isinstance(document, Mapping) else None
            if isinstance(projects, Mapping):
                trusted = {
                    os.path.normcase(os.path.realpath(str(project)))
                    for project, state in projects.items()
                    if isinstance(state, Mapping) and state.get("trust_level") == "trusted"
                }
        _hooks_file(c, base, "user", None, "")
        for name in INSTRUCTIONS:
            _instructions(c, base, name, "user", None)
        rules, _ = c.reach(base, "rules", HARNESS, "user")
        if rules is not None and rules.is_dir():
            c.markdown_tree(rules, "rules", HARNESS, "user", suffixes=())
        _skills(c, base, "skills", "user", None)
        plugins, _ = c.reach(base, "plugins", HARNESS, "user")
        if plugins is not None and plugins.is_dir():
            _plugins(c, plugins, _enabled_plugins(document))
    if paths.agents_home.is_dir():
        _skills(c, paths.agents_home, "skills", "user", None)
    return trusted


def collect_project(c: Collector, repo: Path, nested: list[Path], trusted: set[str]) -> None:
    codex_dir, _ = c.reach(repo, ".codex", HARNESS, "project", repo)
    if codex_dir is not None and codex_dir.is_dir():
        path, link = c.reach(codex_dir, "config.toml", HARNESS, "project", repo)
        if path is not None:
            _config(c, path, "project", ".codex/config.toml", link, repo)
        _hooks_file(c, codex_dir, "project", repo, ".codex/")
        _skills(c, codex_dir, "skills", "project", repo, {"legacy_location": True})
        if os.path.normcase(os.path.realpath(repo)) not in trusted:
            c.finding(
                "info",
                "codex-untrusted-project",
                codex_dir,
                "Repository has .codex/ config but is not trusted in Codex user config",
            )
    # AGENTS.md is read by Codex and can also be loaded by Claude Code.
    for name in INSTRUCTIONS:
        _instructions(c, repo, name, "project", repo, "shared" if name == "AGENTS.md" else HARNESS)
    _skills(c, repo, ".agents/skills", "project", repo)
    for directory in nested:
        relative = directory.relative_to(repo).as_posix()
        for name in INSTRUCTIONS:
            if (directory / name).is_file():
                _instructions(
                    c,
                    repo,
                    f"{relative}/{name}",
                    "project",
                    repo,
                    "shared" if name == "AGENTS.md" else HARNESS,
                )
        if (directory / ".agents" / "skills").is_dir():
            _skills(c, repo, f"{relative}/.agents/skills", "project", repo, {"nested": relative})
