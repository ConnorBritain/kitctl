"""Run the usage adapters and aggregate per primitive per repository."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kitctl.paths import Paths
from kitctl.usage import UsageRecord, attribute
from kitctl.usage import claude as claude_usage
from kitctl.usage import codex as codex_usage
from kitctl.usage.cache import ScanStats, UsageCache

ADAPTERS = ((claude_usage, "claude_config_dir"), (codex_usage, "codex_home"))


def mine(
    paths: Paths,
    repos: Iterable[Path],
    since: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Scan session logs incrementally and return records plus per-adapter statistics."""
    repos = list(repos)
    records: list[UsageRecord] = []
    adapters: dict[str, Any] = {}
    for module, attr in ADAPTERS:
        base: Path = getattr(paths, attr)
        location = paths.kit_cache_dir / f"usage-{module.HARNESS}.json" if use_cache else None
        cache = UsageCache(location, module.HARNESS, module.ADAPTER_VERSION)
        stats: ScanStats = cache.scan(module.files(base), module.parse)
        try:
            cache.save()
        except OSError:
            pass
        malformed, unknown = cache.totals()
        extras = {
            key: sum(int(state.state.get(key, 0)) for state in cache.files.values())
            for key in ("namespaced_calls", "unrecognized_items")
        }
        adapters[module.HARNESS] = {
            **asdict(stats),
            "adapter_version": module.ADAPTER_VERSION,
            "verified_versions": list(module.VERIFIED_VERSIONS),
            "malformed_total": malformed,
            "unknown_shape_total": unknown,
            **extras,
        }
        records.extend(_aggregate(module.HARNESS, cache, repos, since))
    records.sort(key=lambda r: (r.harness, r.kind, r.name, r.repo or ""))
    return {"since": since, "records": [r.to_dict() for r in records], "adapters": adapters}


def _aggregate(
    harness: str, cache: UsageCache, repos: list[Path], since: str | None
) -> list[UsageRecord]:
    totals: dict[tuple[str, str, str | None], list[Any]] = {}
    sessions: dict[tuple[str, str, str | None], set[str]] = defaultdict(set)
    attributed: dict[str, str | None] = {}
    for path, state in cache.files.items():
        for key, (count, first, last) in state.agg.items():
            kind, name, cwd, day = key.split("\x1f")
            if since and day and day < since:
                continue
            if cwd not in attributed:
                attributed[cwd] = attribute(cwd, repos)
            ident = (kind, name, attributed[cwd])
            slot = totals.setdefault(ident, [0, first, last])
            slot[0] += count
            if first and (not slot[1] or first < slot[1]):
                slot[1] = first
            if last > slot[2]:
                slot[2] = last
            sessions[ident].add(path)
    return [
        UsageRecord(
            harness, kind, name, repo, calls, len(sessions[(kind, name, repo)]), first, last
        )
        for (kind, name, repo), (calls, first, last) in totals.items()
    ]
