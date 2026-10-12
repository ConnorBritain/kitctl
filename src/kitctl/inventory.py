"""Read-only discovery across harness config dirs and repositories.

Only metadata, hashes, and findings are reported; configuration values never are.
"""

from __future__ import annotations

import os
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
    roots: list[Path] | None, settings: kit_config.UserConfig | None
) -> tuple[list[Path], str]:
    if roots:
        for root in roots:
            if not root.expanduser().is_dir():
                raise ValueError(f"Inventory root is not a directory: {root}")
        return [root.expanduser() for root in roots], "explicit"
    if settings and settings.repo_parents:
        return discover.find_repos(settings.parent_paths(), settings.discovery_depth), "config"
    return [Path.cwd()], "cwd"


def inventory(
    roots: list[Path] | None = None,
    *,
    use_cache: bool = True,
    settings: kit_config.UserConfig | None = None,
    nested_depth: int = 6,
) -> dict[str, Any]:
    paths = kit_paths.resolve()
    if settings is None:
        settings = kit_config.load(paths.kit_config_file)
    repos, mode = resolve_repos(roots, settings)
    repos = list(normalize_roots(repos))
    home = Path(os.path.realpath(paths.home))
    project_repos = [repo for repo in repos if repo != home]
    warehouse = (settings.warehouse_path() if settings else None) or paths.default_warehouse
    harness_dirs = [*paths.harness_dirs, *paths.claude_managed, *paths.codex_managed]
    scan_roots = normalize_roots([*repos, *harness_dirs])
    allowed = normalize_roots([*repos, *harness_dirs, warehouse])
    cache = HashCache(paths.kit_cache_dir / "hashes.json") if use_cache else HashCache()
    c = Collector(scan_roots, allowed, cache)

    claude.collect_user(c, paths)
    trusted = codex.collect_user(c, paths)
    for repo in project_repos:
        nested = list(discover.nested_dirs(repo, nested_depth))
        claude.collect_project(c, repo, nested)
        codex.collect_project(c, repo, nested, trusted)
    _case_collisions(c)
    try:
        cache.save()
    except OSError as error:
        c.finding("info", "cache-unwritable", str(cache.location), error.strerror or "")

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
        },
        "entries": [entry.to_dict() for entry in c.entries],
        "findings": [finding.to_dict() for finding in c.findings],
    }
