"""Find repositories under configured parent directories, and nested instruction locations."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PRUNE = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "out",
        ".tox",
        ".cache",
        "__pycache__",
        "vendor",
        "Pods",
        "DerivedData",
    }
)


def _subdirs(directory: Path) -> list[Path]:
    try:
        with os.scandir(directory) as entries:
            found = []
            for entry in entries:
                if entry.name in PRUNE or entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():  # follows links; cycles are cut by ``visited``
                        found.append(Path(entry.path))
                except OSError:
                    continue
            return sorted(found)
    except OSError:
        return []


def _identity(directory: Path) -> tuple[int, int] | str:
    """Stable directory identity; case-insensitive filesystems give one dir many spellings."""
    try:
        info = os.stat(directory)
    except OSError:
        return os.path.realpath(directory)
    if info.st_ino:
        return (info.st_dev, info.st_ino)
    return os.path.normcase(os.path.realpath(directory))


def is_repo(directory: Path) -> bool:
    return (directory / ".git").exists()


def is_worktree(directory: Path) -> bool:
    """A linked git worktree: ``.git`` is a file pointing into ``.../worktrees/<name>``."""
    marker = directory / ".git"
    if not marker.is_file():
        return False
    try:
        head = marker.read_text("utf-8", errors="replace")[:4096]
    except OSError:
        return False
    return "gitdir:" in head and "/worktrees/" in head.replace("\\", "/")


def find_repos(
    parents: Iterable[Path], max_depth: int = 3, include_worktrees: bool = False
) -> list[Path]:
    """Directories containing ``.git`` up to ``max_depth`` below each parent; never nested."""
    return find_repos_detail(parents, max_depth, include_worktrees)[0]


def find_repos_detail(
    parents: Iterable[Path], max_depth: int = 3, include_worktrees: bool = False
) -> tuple[list[Path], list[Path]]:
    """``(repos, skipped_worktrees)``. Worktrees are skipped unless ``include_worktrees``."""
    repos: list[Path] = []
    skipped: list[Path] = []
    visited: set[tuple[int, int] | str] = set()
    for parent in parents:
        frontier = [(parent.expanduser(), 0)]
        while frontier:
            directory, depth = frontier.pop()
            identity = _identity(directory)
            if identity in visited:
                continue
            visited.add(identity)
            if is_repo(directory):
                if include_worktrees or not is_worktree(directory):
                    repos.append(directory)
                else:
                    skipped.append(directory)
                continue
            if depth < max_depth:
                frontier.extend((child, depth + 1) for child in _subdirs(directory))
    return sorted(set(repos)), sorted(set(skipped))


MARKERS = frozenset({"CLAUDE.md", "AGENTS.md", "AGENTS.override.md", ".claude", ".agents"})


@dataclass(frozen=True)
class Nested:
    """A directory inside a repo holding at least one nested-instruction marker."""

    path: Path
    relative: str
    markers: frozenset[str]


def _listing(directory: str) -> tuple[bool, frozenset[str], list[tuple[str, bool]]]:
    """``(has_git, markers, [(subdir name, is_link)])`` from one ``scandir``."""
    with os.scandir(directory) as listing:
        entries = list(listing)
    names = {entry.name for entry in entries}
    subdirs: list[tuple[str, bool]] = []
    for entry in entries:
        if entry.name in PRUNE or entry.name.startswith("."):
            continue
        try:
            if entry.is_symlink():
                if entry.is_dir():
                    subdirs.append((entry.name, True))
            elif entry.is_dir(follow_symlinks=False):
                subdirs.append((entry.name, False))
        except OSError:
            continue
    return ".git" in names, frozenset(names & MARKERS), subdirs


def nested_markers(repo: Path, max_depth: int = 6) -> list[Nested]:
    """Directories below ``repo`` (to ``max_depth``) that contain instruction markers.

    One ``scandir`` per directory yields its subdirectories, its markers, and whether it is
    a nested repository (skipped), so callers never stat candidate files themselves.
    """
    results: list[Nested] = []
    visited: set[tuple[int, int] | str] = {_identity(repo)}
    stack: list[tuple[str, str, int]] = [(str(repo), "", 0)]
    while stack:
        directory, relative, depth = stack.pop()
        try:
            has_git, markers, subdirs = _listing(directory)
        except OSError:
            continue
        if depth > 0:
            if has_git:
                continue  # a nested repository or submodule
            if markers:
                results.append(Nested(Path(directory), relative, markers))
        if depth >= max_depth:
            continue
        for name, is_link in subdirs:
            path = os.path.join(directory, name)
            if is_link:  # only links can form cycles
                identity = _identity(Path(path))
                if identity in visited:
                    continue
                visited.add(identity)
            child = f"{relative}/{name}" if relative else name
            stack.append((path, child, depth + 1))
    return sorted(results, key=lambda item: item.relative)
