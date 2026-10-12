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


def find_repos(parents: Iterable[Path], max_depth: int = 3) -> list[Path]:
    """Directories containing ``.git`` up to ``max_depth`` below each parent; never nested."""
    repos: list[Path] = []
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
                repos.append(directory)
                continue
            if depth < max_depth:
                frontier.extend((child, depth + 1) for child in _subdirs(directory))
    return sorted(set(repos))


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
