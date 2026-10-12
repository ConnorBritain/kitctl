"""Codex source adapter (best effort).

Unverified locally: the cloud build has no Codex CLI and cannot reach the Codex docs.
See docs/followups.md; add the checked version to VERIFIED_VERSIONS once confirmed.
"""

from __future__ import annotations

import os
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
VERIFIED_VERSIONS: tuple[str, ...] = ()

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
        list(c.skills_dir(path, HARNESS, scope, repo, None, extra))


def _plugins(c: Collector, directory: Path, depth: int = 0) -> None:
    """Record plugin roots (portable plugin.json or .codex-plugin/plugin.json), 3 levels deep."""
    for root in c.children(directory):
        manifest = next(
            (
                m
                for m in (root / ".codex-plugin" / "plugin.json", root / "plugin.json")
                if m.is_file()
            ),
            None,
        )
        if manifest is None:
            if depth < 2 and root.is_dir() and not root.name.startswith("."):
                _plugins(c, root, depth + 1)
            continue
        document = c.read_json(manifest)
        name = document.get("name") if isinstance(document, Mapping) else None
        pid = f"{name or root.name}@codex"
        c.add(
            Entry(
                "plugin",
                HARNESS,
                "user",
                pid,
                str(root),
                c.digest(manifest),
                HASH_SCHEME,
                metadata={"manifest": manifest.relative_to(root).as_posix(), "verified": False},
            )
        )
        if (root / "skills").is_dir():
            list(c.skills_dir(root / "skills", HARNESS, "plugin", None, pid))
        if (root / "mcp.json").is_file():
            document = c.read_json(root / "mcp.json")
            if isinstance(document, Mapping) and isinstance(document.get("mcpServers"), Mapping):
                document = document["mcpServers"]
            mcp_servers(c, document, root / "mcp.json", "plugin", None, pid, harness=HARNESS)


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
        _skills(c, base, "skills", "user", None, {"legacy_location": True})
        plugins, _ = c.reach(base, "plugins", HARNESS, "user")
        if plugins is not None and plugins.is_dir():
            _plugins(c, plugins)
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
