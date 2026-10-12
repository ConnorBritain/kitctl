"""Symlink- and junction-aware filesystem helpers built on os.scandir.

Links are first-class: callers record them, and read through a link only when its
resolved target lies inside an allowed root (scanned roots, harness dirs, warehouse).
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from kitctl.hashing import is_junk

_REPARSE_POINT = 0x400


def is_junction(path: Path) -> bool:
    if sys.version_info >= (3, 12):
        return os.path.isjunction(path)
    if os.name != "nt":
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT) and not stat.S_ISLNK(info.st_mode)


def is_link(path: Path) -> bool:
    return path.is_symlink() or is_junction(path)


def within(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


@dataclass(frozen=True)
class Link:
    path: Path
    target: Path
    exists: bool
    is_dir: bool
    allowed: bool

    @property
    def readable(self) -> bool:
        return self.exists and self.allowed


def inspect_link(path: Path, allowed: Iterable[Path]) -> Link:
    target = Path(os.path.realpath(path))
    exists = target.exists()
    return Link(path, target, exists, exists and target.is_dir(), within(target, allowed))


def normalize_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    """Resolve roots so link targets (also resolved) compare reliably."""
    return tuple(sorted({Path(os.path.realpath(root.expanduser())) for root in roots}))


@dataclass
class Traversal:
    """Outcome of walking ``root / relative`` one component at a time."""

    links: list[Link] = field(default_factory=list)
    blocked: Link | None = None


def traverse(root: Path, relative: str, allowed: Iterable[Path]) -> Traversal:
    """Record every link on the way to ``root / relative``; stop at one we may not read."""
    allowed = tuple(allowed)
    result = Traversal()
    current = root
    for part in Path(relative).parts:
        current = current / part
        if not is_link(current):
            if not current.exists():
                break
            continue
        link = inspect_link(current, allowed)
        result.links.append(link)
        if not link.readable:
            result.blocked = link
            break
    return result


@dataclass(frozen=True)
class TreeFile:
    relative: str
    path: Path


@dataclass
class Tree:
    files: list[TreeFile] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    """Relative names of links that were recorded but not read (outside roots or broken)."""


def walk_tree(directory: Path, allowed: Iterable[Path], max_depth: int = 32) -> Tree:
    """Collect non-junk files under ``directory``, following only readable links, cycle-safe."""
    allowed = tuple(allowed)
    tree = Tree()
    visited = {os.path.realpath(directory)}

    def visit(current: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        with os.scandir(current) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            relative = f"{prefix}{entry.name}"
            linked = entry.is_symlink() or is_junction(path)
            if linked:
                link = inspect_link(path, allowed)
                tree.links.append(link)
                if not link.readable:
                    tree.external.append(relative)
                    continue
                is_dir = link.is_dir
            else:
                is_dir = entry.is_dir(follow_symlinks=False)
            if is_junk(entry.name, is_dir):
                continue
            if is_dir:
                real = os.path.realpath(path)
                if real in visited:
                    continue
                visited.add(real)
                visit(path, f"{relative}/", depth + 1)
            elif linked or entry.is_file(follow_symlinks=False):
                tree.files.append(TreeFile(relative, path))

    visit(directory, "", 0)
    return tree


def iter_dir(directory: Path) -> Iterator[Path]:
    with os.scandir(directory) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        yield directory / name
