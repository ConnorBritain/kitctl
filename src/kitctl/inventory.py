"""Read-only discovery across harness config dirs and repositories.

Only metadata, hashes, and findings are reported; configuration values never are.
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from kitctl import __version__, discover
from kitctl import config as kit_config
from kitctl import paths as kit_paths
from kitctl.collect import Collector
from kitctl.hashing import HASH_SCHEME, HashCache
from kitctl.model import SCHEMA_VERSION
from kitctl.scan import normalize_roots
from kitctl.sources import claude, codex


class _Clock:
    def __init__(self, sink: dict[str, float] | None) -> None:
        self.sink = sink
        self.last = time.perf_counter()

    def lap(self, name: str, accumulate: bool = False) -> None:
        now = time.perf_counter()
        if self.sink is not None:
            elapsed = now - self.last
            self.sink[name] = self.sink.get(name, 0.0) + elapsed if accumulate else elapsed
        self.last = now


def _case_collisions(c: Collector) -> None:
    groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for entry in c.entries:
        if entry.kind == "link":
            continue
        folded = unicodedata.normalize("NFC", entry.name).casefold()
        groups[(entry.kind, entry.scope, os.path.dirname(entry.path).casefold() + folded)].add(
            entry.name
        )
    for (kind, _, _), names in groups.items():
        if len(names) > 1:
            c.finding(
                "warning",
                "case-collision",
                ", ".join(sorted(names)),
                f"{kind} names differ only by case; they collide on case-insensitive filesystems",
            )


def resolve_repos(
    roots: list[Path] | None,
    settings: kit_config.UserConfig | None,
    include_worktrees: bool | None = None,
) -> tuple[list[Path], str, list[Path]]:
    """``(repos, mode, skipped_worktrees)``."""
    if roots:
        for root in roots:
            if not root.expanduser().is_dir():
                raise ValueError(f"Inventory root is not a directory: {root}")
        return [root.expanduser() for root in roots], "explicit", []
    if settings and settings.repo_parents:
        include = settings.include_worktrees if include_worktrees is None else include_worktrees
        repos, skipped = discover.find_repos_detail(
            settings.parent_paths(), settings.discovery_depth, include
        )
        return repos, "config", skipped
    return [Path.cwd()], "cwd", []


def inventory(
    roots: list[Path] | None = None,
    *,
    use_cache: bool = True,
    settings: kit_config.UserConfig | None = None,
    nested_depth: int | None = None,
    include_worktrees: bool | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run discovery. If ``timings`` is given, it is filled with per-phase seconds/counts."""
    clock = _Clock(timings)
    paths = kit_paths.resolve()
    if settings is None:
        settings = kit_config.load(paths.kit_config_file)
    repos, mode, skipped_worktrees = resolve_repos(roots, settings, include_worktrees)
    clock.lap("discover_repos")
    repos = list(normalize_roots(repos))
    home = Path(os.path.realpath(paths.home))
    project_repos = [repo for repo in repos if repo != home]
    warehouse = (settings.warehouse_path() if settings else None) or paths.default_warehouse
    harness_dirs = [*paths.harness_dirs, *paths.claude_managed, *paths.codex_managed]
    scan_roots = normalize_roots([*repos, *harness_dirs])
    allowed = normalize_roots([*repos, *harness_dirs, warehouse])
    cache = HashCache(paths.kit_cache_dir / "hashes.json") if use_cache else HashCache()
    c = Collector(scan_roots, allowed, cache)
    depth = nested_depth if nested_depth is not None else (settings.nested_depth if settings else 6)
    clock.lap("load_cache")

    claude.collect_user(c, paths)
    clock.lap("claude_user")
    trusted = codex.collect_user(c, paths)
    clock.lap("codex_user")
    for repo in project_repos:
        nested = discover.nested_markers(repo, depth)
        clock.lap("nested_walk", accumulate=True)
        claude.collect_project(c, repo, nested)
        clock.lap("claude_projects", accumulate=True)
        codex.collect_project(c, repo, nested, trusted)
        clock.lap("codex_projects", accumulate=True)
    _case_collisions(c)
    clock.lap("analysis")
    try:
        cache.save()
    except OSError as error:
        c.finding("info", "cache-unwritable", str(cache.location), error.strerror or "")
    clock.lap("save_cache")
    if timings is not None:
        timings.update(
            repos=len(project_repos),
            entries=len(c.entries),
            files_hashed=cache.misses,
            files_cached=cache.hits,
            bytes_hashed=cache.bytes_hashed,
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": f"kitctl {__version__}",
        "hash_scheme": HASH_SCHEME,
        "adapters": {
            "claude": {"verified_versions": list(claude.VERIFIED_VERSIONS)},
            "codex": {"verified_versions": list(codex.VERIFIED_VERSIONS)},
        },
        "roots": {
            "mode": mode,
            "repos": [str(repo) for repo in project_repos],
            "claude_config_dir": str(paths.claude_config_dir),
            "codex_home": str(paths.codex_home),
            "skipped_worktrees": len(skipped_worktrees),
        },
        "entries": [entry.to_dict() for entry in c.entries],
        "findings": [finding.to_dict() for finding in c.findings],
    }
