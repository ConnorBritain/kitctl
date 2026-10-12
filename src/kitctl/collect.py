"""Shared collection machinery for harness source adapters."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kitctl.hashing import HASH_SCHEME, HashCache, tree_digest
from kitctl.model import Entry, Finding
from kitctl.safemeta import frontmatter
from kitctl.scan import Link, is_junction, iter_dir, traverse, walk_tree, within


class Collector:
    """Accumulates entries and findings; owns link policy, hashing, and parse errors."""

    def __init__(
        self,
        scan_roots: tuple[Path, ...],
        allowed: tuple[Path, ...],
        cache: HashCache | None = None,
    ) -> None:
        self.scan_roots = scan_roots
        self.allowed = allowed
        self.cache = cache or HashCache()
        self.entries: list[Entry] = []
        self.findings: list[Finding] = []
        self._links: set[Path] = set()
        self._seen: set[tuple[str, str, str, str]] = set()

    # -- recording -------------------------------------------------------

    def add(self, entry: Entry) -> None:
        identity = (entry.kind, entry.scope, entry.path, entry.name)
        if identity in self._seen:
            return
        self._seen.add(identity)
        self.entries.append(entry)

    def finding(self, severity: str, code: str, path: Path | str, message: str) -> None:
        self.findings.append(Finding(severity, code, str(path), message))

    def link(self, link: Link, base: Path, harness: str, scope: str, repo: Path | None) -> None:
        if link.path in self._links:
            return
        self._links.add(link.path)
        try:
            name = link.path.relative_to(base).as_posix()
        except ValueError:
            name = link.path.name
        self.add(
            Entry(
                "link",
                harness,
                scope,
                name,
                str(link.path),
                None,
                None,
                str(link.target),
                within(link.target, self.scan_roots),
                repo=str(repo) if repo else None,
                metadata={"target_exists": link.exists, "target_allowed": link.allowed},
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

    # -- reaching paths ----------------------------------------------------

    def reach(
        self, base: Path, relative: str, harness: str, scope: str, repo: Path | None = None
    ) -> tuple[Path | None, Link | None]:
        """Return ``base/relative`` if it exists and every link on the way is readable.

        Links crossed are recorded. The second value is the link at the final component.
        """
        route = traverse(base, relative, self.allowed)
        for link in route.links:
            self.link(link, base, harness, scope, repo)
        if route.blocked:
            return None, None
        path = base / relative
        if not path.exists():
            return None, None
        final = next((link for link in route.links if link.path == path), None)
        return path, final

    def children(self, directory: Path) -> list[Path]:
        try:
            return list(iter_dir(directory))
        except OSError as error:
            self.finding("error", "unreadable", directory, f"Cannot list: {error.strerror}")
            return []

    def child(
        self, directory: Path, name: str, harness: str, scope: str, repo: Path | None
    ) -> tuple[Path | None, Link | None]:
        return self.reach(directory, name, harness, scope, repo)

    # -- reading -----------------------------------------------------------

    def digest(self, path: Path, stat: os.stat_result | None = None) -> str | None:
        try:
            return self.cache.digest(path, stat) if self.cache.location else _plain_digest(path)
        except OSError as error:
            self.finding("error", "unreadable", path, f"Cannot read: {error.strerror}")
            return None

    def read_text(self, path: Path, limit: int = 1 << 20) -> str | None:
        try:
            with path.open("rb") as handle:
                return handle.read(limit).decode("utf-8", errors="replace")
        except OSError as error:
            self.finding("error", "unreadable", path, f"Cannot read: {error.strerror}")
            return None

    def read_json(self, path: Path) -> Any:
        text = self.read_text(path, limit=64 << 20)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError as error:
            self.finding("error", "invalid-json", path, f"Invalid JSON at line {_line(error)}")
            return None

    def read_toml(self, path: Path) -> dict[str, Any] | None:
        text = self.read_text(path, limit=16 << 20)
        if text is None:
            return None
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            self.finding("error", "invalid-toml", path, f"Invalid TOML: {str(error)[:120]}")
            return None

    # -- common entry builders ----------------------------------------------

    def file_entry(
        self,
        kind: str,
        harness: str,
        scope: str,
        name: str,
        path: Path,
        link: Link | None = None,
        repo: Path | None = None,
        provided_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Entry | None:
        if not path.is_file():
            self.finding("warning", "not-a-file", path, "Expected a regular file")
            return None
        digest = self.digest(path)
        if digest is None:
            return None
        entry = Entry(
            kind,
            harness,
            scope,
            name,
            str(path),
            digest,
            HASH_SCHEME,
            str(link.target) if link else None,
            within(link.target, self.scan_roots) if link else None,
            provided_by,
            str(repo) if repo else None,
            metadata or {},
        )
        self.add(entry)
        return entry

    def skill_entry(
        self,
        harness: str,
        scope: str,
        directory: Path,
        link: Link | None = None,
        repo: Path | None = None,
        provided_by: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Entry | None:
        try:
            tree = walk_tree(directory, self.allowed)
        except OSError as error:
            self.finding("error", "unreadable", directory, f"Cannot read skill: {error.strerror}")
            return None
        if not any(item.relative == "SKILL.md" for item in tree.files):
            return None
        pairs: list[tuple[str, str]] = []
        size = 0
        for item in tree.files:
            try:
                info = item.stat or item.path.stat()
            except OSError as error:
                self.finding("error", "unreadable", item.path, f"Cannot read: {error.strerror}")
                return None
            digest = self.digest(item.path, info)
            if digest is None:
                return None
            pairs.append((item.relative, digest))
            size += info.st_size
        pairs += [(name, "external-link") for name in tree.external]
        for inner in tree.links:
            self.link(inner, directory, harness, scope, repo)
        manifest_digest = dict(pairs).get("SKILL.md", "")
        front = self.cache.memo.get(manifest_digest)
        if front is None:
            front = frontmatter(self.read_text(directory / "SKILL.md", limit=64 << 10) or "")
            if manifest_digest:
                self.cache.remember(manifest_digest, front)
        meta: dict[str, Any] = {
            "frontmatter_name": front.get("name"),
            "description": front.get("description"),
            "file_count": len(tree.files),
            "total_size": size,
        }
        if extra:
            meta.update(extra)
        entry = Entry(
            "skill",
            harness,
            scope,
            directory.name,
            str(directory),
            tree_digest(pairs),
            HASH_SCHEME,
            str(link.target) if link else None,
            within(link.target, self.scan_roots) if link else None,
            provided_by,
            str(repo) if repo else None,
            meta,
        )
        self.add(entry)
        return entry

    def skills_dir(
        self,
        directory: Path,
        harness: str,
        scope: str,
        repo: Path | None = None,
        provided_by: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Iterator[Path]:
        """Record each ``<name>/SKILL.md`` skill; yield child dirs that are not skills."""
        for child in self.children(directory):
            if child.is_symlink() or is_junction(child):
                path, link = self.child(directory, child.name, harness, scope, repo)
                if path is None or not path.is_dir():
                    continue
            elif child.is_dir():
                path, link = child, None
            else:
                continue
            if self.skill_entry(harness, scope, path, link, repo, provided_by, extra) is None:
                yield path

    def markdown_tree(
        self,
        directory: Path,
        kind: str,
        harness: str,
        scope: str,
        repo: Path | None = None,
        provided_by: str | None = None,
        suffixes: tuple[str, ...] = (".md",),
    ) -> None:
        """Record every matching file below ``directory`` (agents, commands, rules, scripts)."""
        try:
            tree = walk_tree(directory, self.allowed, max_depth=8)
        except OSError as error:
            self.finding("error", "unreadable", directory, f"Cannot list: {error.strerror}")
            return
        for inner in tree.links:
            self.link(inner, directory, harness, scope, repo)
        for item in tree.files:
            if suffixes and not item.relative.endswith(suffixes):
                continue
            name = item.relative
            for suffix in suffixes:
                if name.endswith(suffix) and suffix:
                    name = name[: -len(suffix)]
                    break
            meta: dict[str, Any] = {"source": "script"} if kind == "hook" else {}
            if kind in {"agent", "command"}:
                front = frontmatter(self.read_text(item.path, limit=64 << 10) or "")
                meta = {k: v for k, v in front.items() if v}
            self.file_entry(kind, harness, scope, name, item.path, None, repo, provided_by, meta)


def _plain_digest(path: Path) -> str:
    from kitctl.hashing import file_digest

    return file_digest(path)


def _line(error: ValueError) -> int:
    return getattr(error, "lineno", 0)
