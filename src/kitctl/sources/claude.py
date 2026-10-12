"""Claude Code source adapter.

Verified against code.claude.com documentation (settings, claude-directory, plugins
reference, plugin loading) and the installed CLI listed in VERIFIED_VERSIONS.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from kitctl.collect import Collector
from kitctl.discover import Nested
from kitctl.hashing import HASH_SCHEME
from kitctl.model import Entry
from kitctl.paths import Paths
from kitctl.safemeta import canonical_hash, iter_hooks, mcp_server, settings
from kitctl.scan import within

HARNESS = "claude"
VERIFIED_VERSIONS = ("2.1.86", "2.1.296")

PROJECT_INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
COMPONENT_DIRS = (("agents", "agent"), ("commands", "command"), ("rules", "rules"))


def _settings_file(
    c: Collector, path: Path, scope: str, name: str, link: Any, repo: Path | None
) -> None:
    document = c.read_json(path)
    meta = settings(document) if document is not None else {"invalid": True}
    c.file_entry("settings", HARNESS, scope, name, path, link, repo, metadata=meta)
    if isinstance(document, Mapping):
        _hooks(c, document.get("hooks"), path, scope, repo, None)


def _hooks(
    c: Collector,
    hooks: Any,
    source: Path,
    scope: str,
    repo: Path | None,
    provided_by: str | None,
) -> None:
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
                provided_by=provided_by,
                repo=str(repo) if repo else None,
                metadata={**meta, "source": "config"},
            )
        )
        if meta.get("via_npx_kitctl"):
            c.finding(
                "warning",
                "hook-npx-latency",
                source,
                "Hook runs `npx kitctl`; install kitctl with uv tool or pipx for hook latency",
            )


def mcp_servers(
    c: Collector,
    servers: Any,
    source: Path,
    scope: str,
    repo: Path | None = None,
    provided_by: str | None = None,
    harness: str = HARNESS,
) -> None:
    if not isinstance(servers, Mapping):
        return
    for name, config in servers.items():
        c.add(
            Entry(
                "mcp_server",
                harness,
                scope,
                str(name),
                str(source),
                canonical_hash(config),
                None,
                provided_by=provided_by,
                repo=str(repo) if repo else None,
                metadata={**mcp_server(config), **command_status(config)},
            )
        )


def command_status(config: Any) -> dict[str, Any]:
    """Whether a stdio server's command resolves, without reporting the command itself."""
    if not isinstance(config, Mapping) or not isinstance(config.get("command"), str):
        return {}
    command = config["command"].strip()
    if not command or "${" in command or "$" in command[:1]:
        return {}  # plugin-root or env-expanded paths resolve only at runtime
    if os.path.isabs(command) or "/" in command or "\\" in command:
        return {"command_on_path": os.path.exists(command)}
    return {"command_on_path": shutil.which(command) is not None}


def _mcp_document(document: Any) -> Any:
    """The server map from an MCP config file: ``{"mcpServers": {...}}`` or a bare map."""
    if not isinstance(document, Mapping):
        return None
    if isinstance(document.get("mcpServers"), Mapping):
        return document["mcpServers"]
    bare = document and all(
        isinstance(value, Mapping) and any(key in value for key in ("command", "url", "type"))
        for value in document.values()
    )
    return document if bare else None


def _instructions(c: Collector, base: Path, relative: str, scope: str, repo: Path | None) -> None:
    path, link = c.reach(base, relative, HARNESS, scope, repo)
    if path is not None:
        c.file_entry("instructions", HARNESS, scope, relative, path, link, repo)


def _components(
    c: Collector, base: Path, scope: str, repo: Path | None, provided_by: str | None = None
) -> None:
    """skills/, agents/, commands/, rules/, and hooks/ scripts under a config dir."""
    skills, _ = c.reach(base, "skills", HARNESS, scope, repo)
    if skills is not None and skills.is_dir():
        for leftover in c.skills_dir(skills, HARNESS, scope, repo, provided_by):
            _skill_container(c, leftover, scope, repo)
    for folder, kind in COMPONENT_DIRS:
        path, _ = c.reach(base, folder, HARNESS, scope, repo)
        if path is not None and path.is_dir():
            c.markdown_tree(path, kind, HARNESS, scope, repo, provided_by)
    hooks, _ = c.reach(base, "hooks", HARNESS, scope, repo)
    if hooks is not None and hooks.is_dir():
        c.markdown_tree(hooks, "hook", HARNESS, scope, repo, provided_by, suffixes=())


def _skill_container(c: Collector, directory: Path, scope: str, repo: Path | None) -> None:
    """A non-skill directory under skills/: a skills-dir plugin or claude.ai synced skills."""
    if (directory / ".claude-plugin" / "plugin.json").is_file():
        _plugin(c, directory, None, "skills-dir", scope, repo, {})
        return
    if directory.name == "synced":
        for account in c.children(directory):
            if account.is_dir():
                list(
                    c.skills_dir(
                        account, HARNESS, scope, repo, "claude.ai:synced", {"synced": True}
                    )
                )


def _manifest_paths(root: Path, value: Any) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    paths = []
    for item in values:
        if isinstance(item, str) and (item.startswith("./") or item == "."):
            candidate = (root / item).resolve()
            if within(candidate, (root.resolve(),)):
                paths.append(candidate)
    return paths


def _plugin(
    c: Collector,
    root: Path,
    plugin_id: str | None,
    origin: str,
    scope: str,
    repo: Path | None,
    record: Mapping[str, Any],
) -> None:
    """Record a plugin and every component it provides."""
    manifest_path = root / ".claude-plugin" / "plugin.json"
    manifest = c.read_json(manifest_path) if manifest_path.is_file() else None
    manifest = manifest if isinstance(manifest, Mapping) else {}
    name = str(manifest.get("name") or root.name)
    pid = plugin_id or f"{name}@{origin}"
    meta: dict[str, Any] = {
        "origin": origin,
        "manifest_name": manifest.get("name"),
        "version": record.get("version") or manifest.get("version"),
        "install_scope": record.get("scope"),
        "description": manifest.get("description"),
    }
    if record.get("projectPath"):
        meta["project_path"] = str(record["projectPath"])
    c.add(
        Entry(
            "plugin",
            HARNESS,
            scope,
            pid,
            str(root),
            c.digest(manifest_path) if manifest_path.is_file() else None,
            HASH_SCHEME if manifest_path.is_file() else None,
            repo=str(repo) if repo else None,
            metadata={k: v for k, v in meta.items() if v is not None},
        )
    )
    sub = "plugin"
    skill_dirs = [root / "skills", *_manifest_paths(root, manifest.get("skills", []))]
    if (root / "SKILL.md").is_file() and not (root / "skills").exists():
        c.skill_entry(HARNESS, sub, root, None, repo, pid)
    for directory in skill_dirs:
        if directory.is_dir():
            if (directory / "SKILL.md").is_file():
                c.skill_entry(HARNESS, sub, directory, None, repo, pid)
            else:
                list(c.skills_dir(directory, HARNESS, sub, repo, pid))
    for key, kind in (("commands", "command"), ("agents", "agent")):
        declared = manifest.get(key)
        targets = [root / key] if declared is None else _manifest_paths(root, declared)
        for target in targets:
            if target.is_dir():
                c.markdown_tree(target, kind, HARNESS, sub, repo, pid)
            elif target.is_file():
                c.file_entry(kind, HARNESS, sub, target.stem, target, None, repo, pid)
    hook_sources: list[Any] = []
    if (root / "hooks" / "hooks.json").is_file():
        hook_sources.append((root / "hooks" / "hooks.json", None))
    declared_hooks = manifest.get("hooks")
    hook_items: list[Any] = declared_hooks if isinstance(declared_hooks, list) else [declared_hooks]
    for item in hook_items:
        if isinstance(item, Mapping):
            hook_sources.append((manifest_path, item))
        elif isinstance(item, str):
            hook_sources.extend((path, None) for path in _manifest_paths(root, item))
    for source, inline in hook_sources:
        document = inline if inline is not None else c.read_json(source)
        _hooks(c, document, source, sub, repo, pid)
    if (root / ".mcp.json").is_file():
        mcp_servers(
            c, _mcp_document(c.read_json(root / ".mcp.json")), root / ".mcp.json", sub, repo, pid
        )
    declared_mcp = manifest.get("mcpServers")
    for item in declared_mcp if isinstance(declared_mcp, list) else [declared_mcp]:
        if isinstance(item, Mapping):
            mcp_servers(c, item, manifest_path, sub, repo, pid)
        elif isinstance(item, str) and item.endswith(".json"):
            for path in _manifest_paths(root, item):
                if path.is_file():
                    mcp_servers(c, _mcp_document(c.read_json(path)), path, sub, repo, pid)


def _install_records(document: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """Tolerant reader for installed_plugins.json (``{"plugins": {id: [record, ...]}}``)."""
    plugins = document.get("plugins") if isinstance(document, Mapping) else None
    if not isinstance(plugins, Mapping):
        return []
    records = []
    for pid, value in plugins.items():
        for record in value if isinstance(value, list) else [value]:
            if isinstance(record, Mapping):
                records.append((str(pid), record))
    return records


def _plugins(c: Collector, paths: Paths) -> None:
    root = paths.claude_plugins_dir
    installed = root / "installed_plugins.json"
    if installed.is_file():
        document = c.read_json(installed)
        records = _install_records(document)
        if document is not None and not records and document not in ({}, {"plugins": {}}):
            c.finding("info", "unknown-shape", installed, "Unrecognized installed_plugins shape")
        for pid, record in records:
            install = record.get("installPath")
            if not isinstance(install, str):
                continue
            path = Path(install)
            project = record.get("projectPath")
            repo = Path(project) if isinstance(project, str) else None
            if not path.is_dir():
                c.finding("warning", "plugin-not-cached", path, f"Installed plugin {pid} missing")
                continue
            _plugin(c, path, pid, pid.partition("@")[2] or "unknown", "user", repo, record)
    known = root / "known_marketplaces.json"
    if known.is_file():
        document = c.read_json(known)
        if isinstance(document, Mapping):
            for name, record in document.items():
                if not isinstance(record, Mapping):
                    record = {}
                raw_source = record.get("source")
                source: Mapping[str, Any] = raw_source if isinstance(raw_source, Mapping) else {}
                meta = {
                    "source_type": source.get("source"),
                    "repo": source.get("repo"),
                    "auto_update": record.get("autoUpdate"),
                }
                url = source.get("url")
                if isinstance(url, str):
                    meta["url_host"] = urlsplit(url).hostname
                c.add(
                    Entry(
                        "marketplace",
                        HARNESS,
                        "user",
                        str(name),
                        str(known),
                        canonical_hash(record.get("source")),
                        None,
                        metadata={k: v for k, v in meta.items() if v is not None},
                    )
                )
    synced = root / "synced"
    if synced.is_dir():
        for account in c.children(synced):
            if not account.is_dir() or account.name.startswith("."):
                continue
            for plugin_root in c.children(account):
                if plugin_root.is_dir() and not plugin_root.name.startswith("."):
                    _plugin(c, plugin_root, None, "synced", "user", None, {})


def collect_user(c: Collector, paths: Paths) -> None:
    base = paths.claude_config_dir
    for directory in paths.claude_managed:
        if not directory.is_dir():
            continue
        for name in ("managed-settings.json",):
            if (directory / name).is_file():
                _settings_file(c, directory / name, "managed", name, None, None)
        if (directory / "managed-mcp.json").is_file():
            document = c.read_json(directory / "managed-mcp.json")
            c.file_entry(
                "settings",
                HARNESS,
                "managed",
                "managed-mcp.json",
                directory / "managed-mcp.json",
                metadata=settings(document),
            )
            mcp_servers(c, _mcp_document(document), directory / "managed-mcp.json", "managed")
        if (directory / "CLAUDE.md").is_file():
            c.file_entry("instructions", HARNESS, "managed", "CLAUDE.md", directory / "CLAUDE.md")
    if base.is_dir():
        for name in ("settings.json", "settings.local.json"):
            path, link = c.reach(base, name, HARNESS, "user")
            if path is not None:
                _settings_file(c, path, "user", name, link, None)
        _instructions(c, base, "CLAUDE.md", "user", None)
        _components(c, base, "user", None)
    if paths.claude_json.is_file():
        document = c.read_json(paths.claude_json)
        if isinstance(document, Mapping):
            projects = document.get("projects")
            c.file_entry(
                "settings",
                HARNESS,
                "user",
                ".claude.json",
                paths.claude_json,
                metadata={
                    "keys": sorted(str(key) for key in document),
                    "project_count": len(projects) if isinstance(projects, Mapping) else 0,
                },
            )
            mcp_servers(c, document.get("mcpServers"), paths.claude_json, "user")
            if isinstance(projects, Mapping):
                for project, state in projects.items():
                    servers = state.get("mcpServers") if isinstance(state, Mapping) else None
                    if isinstance(servers, Mapping) and servers and not Path(str(project)).exists():
                        c.finding(
                            "info",
                            "stale-local-project",
                            paths.claude_json,
                            f"{len(servers)} local MCP server(s) configured for a project path "
                            f"that no longer exists: {project}",
                        )
                    if isinstance(state, Mapping):
                        mcp_servers(
                            c,
                            state.get("mcpServers"),
                            paths.claude_json,
                            "local",
                            Path(str(project)),
                        )
    _plugins(c, paths)


def collect_project(c: Collector, repo: Path, nested: list[Nested]) -> None:
    claude_dir, _ = c.reach(repo, ".claude", HARNESS, "project", repo)
    if claude_dir is not None and claude_dir.is_dir():
        for name, scope in (("settings.json", "project"), ("settings.local.json", "local")):
            path, link = c.reach(claude_dir, name, HARNESS, scope, repo)
            if path is not None:
                _settings_file(c, path, scope, f".claude/{name}", link, repo)
        _components(c, claude_dir, "project", repo)
    mcp, link = c.reach(repo, ".mcp.json", HARNESS, "project", repo)
    if mcp is not None:
        document = c.read_json(mcp)
        c.file_entry(
            "settings",
            HARNESS,
            "project",
            ".mcp.json",
            mcp,
            link,
            repo,
            metadata=settings(document) if document is not None else {"invalid": True},
        )
        mcp_servers(c, _mcp_document(document), mcp, "project", repo)
    for relative in PROJECT_INSTRUCTIONS:
        scope = "local" if relative == "CLAUDE.local.md" else "project"
        _instructions(c, repo, relative, scope, repo)
    for nested_dir in nested:
        relative = nested_dir.relative
        if "CLAUDE.md" in nested_dir.markers:
            _instructions(c, repo, f"{relative}/CLAUDE.md", "project", repo)
        if ".claude" in nested_dir.markers and (nested_dir.path / ".claude" / "skills").is_dir():
            skills, _ = c.reach(repo, f"{relative}/.claude/skills", HARNESS, "project", repo)
            if skills is not None:
                list(c.skills_dir(skills, HARNESS, "project", repo, None, {"nested": relative}))
