"""Read-only, explicitly scoped discovery. Never serialize configuration values."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict

from kitctl import paths as kit_paths
from kitctl.hashing import HASH_SCHEME, HashCache, file_digest, tree_digest
from kitctl.scan import Link, iter_dir, normalize_roots, traverse, walk_tree, within

CONFIG_PATHS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude.json",
    ".mcp.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    "CLAUDE.md",
    "AGENTS.md",
    "kit.toml",
    "kit.lock",
)
SKILL_PATHS = (".claude/skills", ".agents/skills", ".codex/skills")


@dataclass(frozen=True)
class Entry:
    kind: str
    name: str
    root: str
    path: str
    content_hash: str | None
    hash_scheme: int | None
    link_target: str | None = None
    target_in_scan: bool | None = None


@dataclass(frozen=True)
class Finding:
    severity: str  # "info" | "warning" | "error"; only errors make a run exit 1
    code: str
    path: str
    message: str


class Report(TypedDict):
    schema_version: int
    hash_scheme: int
    entries: list[dict[str, Any]]
    findings: list[dict[str, str]]


class _Collector:
    def __init__(self, scan_roots: tuple[Path, ...], allowed: tuple[Path, ...]) -> None:
        self.scan_roots = scan_roots
        self.allowed = allowed
        self.entries: list[Entry] = []
        self.findings: list[Finding] = []
        self._links: set[Path] = set()
        self.cache = HashCache()

    def digest(self, path: Path) -> str:
        return self.cache.digest(path) if self.cache.location else file_digest(path)

    def link(self, link: Link, root: Path) -> None:
        if link.path in self._links:
            return
        self._links.add(link.path)
        name = link.path.relative_to(root).as_posix()
        target_in_scan = within(link.target, self.scan_roots)
        self.entries.append(
            Entry(
                "link",
                name,
                str(root),
                str(link.path),
                None,
                None,
                str(link.target),
                target_in_scan,
            )
        )
        if not link.exists:
            self.finding("warning", "broken-link", link.path, f"Link target missing: {link.target}")
        elif not link.allowed:
            self.finding(
                "info",
                "external-link",
                link.path,
                "Link target is outside allowed roots; recorded without reading",
            )

    def finding(self, severity: str, code: str, path: Path, message: str) -> None:
        self.findings.append(Finding(severity, code, str(path), message))


def _config(collector: _Collector, root: Path, relative: str) -> None:
    route = traverse(root, relative, collector.allowed)
    for link in route.links:
        collector.link(link, root)
    if route.blocked:
        return
    path = root / relative
    if not path.exists():
        return
    if not path.is_file():
        collector.finding("warning", "not-a-file", path, "Expected a regular file")
        return
    via = next((link for link in route.links if link.path == path), None)
    try:
        digest = collector.digest(path)
    except OSError as error:
        collector.finding("error", "unreadable", path, f"Cannot read: {error.strerror}")
        return
    collector.entries.append(
        Entry(
            "config",
            relative,
            str(root),
            str(path),
            digest,
            HASH_SCHEME,
            str(via.target) if via else None,
            within(via.target, collector.scan_roots) if via else None,
        )
    )


def _skill(collector: _Collector, root: Path, skill: Path, link: Link | None) -> None:
    try:
        tree = walk_tree(skill, collector.allowed)
        if not any(item.relative == "SKILL.md" for item in tree.files):
            return
        digest = tree_digest(
            [(item.relative, collector.digest(item.path)) for item in tree.files]
            + [(name, "external-link") for name in tree.external]
        )
    except OSError as error:
        collector.finding("error", "unreadable", skill, f"Cannot read complete skill: {error}")
        return
    for inner in tree.links:
        collector.link(inner, root)
    collector.entries.append(
        Entry(
            "skill",
            skill.name,
            str(root),
            str(skill),
            digest,
            HASH_SCHEME,
            str(link.target) if link else None,
            within(link.target, collector.scan_roots) if link else None,
        )
    )


def _skills(collector: _Collector, root: Path, relative: str) -> None:
    route = traverse(root, relative, collector.allowed)
    for crossed in route.links:
        collector.link(crossed, root)
    directory = root / relative
    if route.blocked or not directory.is_dir():
        return
    try:
        children = list(iter_dir(directory))
    except OSError as error:
        collector.finding("error", "unreadable", directory, f"Cannot list: {error.strerror}")
        return
    for child in children:
        step = traverse(directory, child.name, collector.allowed)
        link = step.links[0] if step.links else None
        if link:
            collector.link(link, root)
            if not link.readable:
                continue
        if child.is_dir():
            _skill(collector, root, child, link)


def allowed_roots(scan_roots: tuple[Path, ...], warehouse: Path | None = None) -> tuple[Path, ...]:
    resolved = kit_paths.resolve()
    extra = [*resolved.harness_dirs, warehouse or resolved.default_warehouse]
    return normalize_roots([*scan_roots, *extra])


def inventory(roots: list[Path], cache: HashCache | None = None) -> Report:
    scan_roots = normalize_roots(roots)
    for root in scan_roots:
        if not root.is_dir():
            raise ValueError(f"Inventory root is not a directory: {root}")
    collector = _Collector(scan_roots, allowed_roots(scan_roots))
    if cache is not None:
        collector.cache = cache
    for root in scan_roots:
        for relative in CONFIG_PATHS:
            _config(collector, root, relative)
        for relative in SKILL_PATHS:
            _skills(collector, root, relative)
    collector.cache.save()
    return {
        "schema_version": 2,
        "hash_scheme": HASH_SCHEME,
        "entries": [asdict(entry) for entry in collector.entries],
        "findings": [asdict(finding) for finding in collector.findings],
    }
