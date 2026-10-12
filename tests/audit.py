"""Runtime guard for tests: fail on writes outside kitctl's dirs and on any subprocess."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_state: dict[str, Any] = {"active": False, "allowed": (), "violations": [], "subprocess": False}
_installed = False


def _allowed(path: object) -> bool:
    if isinstance(path, int):
        return True  # already-open descriptor
    text = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else str(path)
    if "__pycache__" in text:
        return True  # the interpreter caching bytecode for lazily imported modules
    real = os.path.realpath(text)
    return any(real == root or real.startswith(root + os.sep) for root in _state["allowed"])


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _state["active"]:
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        if writing and not _allowed(path):
            _state["violations"].append(f"write {path}")
    elif event in {"os.rename", "os.replace"}:
        for path in args[:2]:
            if not _allowed(path):
                _state["violations"].append(f"{event} {path}")
    elif event == "os.mkdir":
        # Creating a parent of an allowed root (e.g. ~/.cache for ~/.cache/kitctl) is fine.
        real = os.path.realpath(os.fsdecode(args[0])) if args else ""
        ancestor = any(root.startswith(real + os.sep) for root in _state["allowed"])
        if args and not ancestor and not _allowed(args[0]):
            _state["violations"].append(f"{event} {args[0]}")
    elif event in {"os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.symlink"}:
        if args and not _allowed(args[0]):
            _state["violations"].append(f"{event} {args[0]}")
    elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"}:
        if not _state["subprocess"]:
            _state["violations"].append(f"{event} {args[0] if args else ''}")


@contextmanager
def guarded(allowed: list[Path], allow_subprocess: bool = False) -> Iterator[list[str]]:
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
    _state.update(
        active=True,
        allowed=tuple(os.path.realpath(p) for p in allowed),
        violations=[],
        subprocess=allow_subprocess,
    )
    try:
        yield _state["violations"]
    finally:
        _state["active"] = False
