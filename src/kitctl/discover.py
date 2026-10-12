"""Find repositories under configured parent directories, and nested instruction locations."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
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


def nested_dirs(repo: Path, max_depth: int = 6) -> Iterator[Path]:
    """Directories inside ``repo`` (excluding the root) where nested instructions may live."""
    visited = {_identity(repo)}
    frontier = [(child, 1) for child in _subdirs(repo)]
    while frontier:
        directory, depth = frontier.pop()
        identity = _identity(directory)
        if identity in visited or is_repo(directory):
            continue
        visited.add(identity)
        yield directory
        if depth < max_depth:
            frontier.extend((child, depth + 1) for child in _subdirs(directory))
