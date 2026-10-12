"""Versioned content hashing that ignores line endings, junk files, and name normalization."""

from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

HASH_SCHEME = 2
"""Scheme 1 (0.0.1): raw bytes, all files. Scheme 2: see module docstring and docs/design.md."""

JUNK_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
JUNK_DIRS = frozenset({".git", "__pycache__"})
JUNK_SUFFIXES = (".pyc",)
_SNIFF = 8192
_CHUNK = 65536


def is_junk(name: str, is_dir: bool) -> bool:
    if is_dir:
        return name in JUNK_DIRS
    return name in JUNK_FILES or name.endswith(JUNK_SUFFIXES)


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _open_with_retry(path: Path, attempts: int = 3, delay: float = 0.05) -> BinaryIO:
    """Open for reading; on Windows retry briefly when another process holds a lock."""
    for attempt in range(attempts):
        try:
            return path.open("rb")
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def file_digest(path: Path) -> str:
    """SHA-256 of a file; text (no NUL in the first 8 KB) has CRLF and CR normalized to LF."""
    digest = hashlib.sha256()
    with _open_with_retry(path) as handle:
        head = handle.read(_SNIFF)
        if b"\0" in head:
            digest.update(head)
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
            return digest.hexdigest()
        pending = head
        while True:
            more = handle.read(_CHUNK)
            data = pending + more
            # Hold back a trailing CR so a CRLF split across reads still normalizes.
            if more and data.endswith(b"\r"):
                data, pending = data[:-1], b"\r"
            else:
                pending = b""
            digest.update(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
            if not more:
                break
    return digest.hexdigest()


def tree_digest(items: Iterable[tuple[str, str]]) -> str:
    """Combine ``(relative POSIX path, digest)`` pairs; order-independent, NFC-normalized."""
    digest = hashlib.sha256()
    for name, value in sorted((nfc(name), value) for name, value in items):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


class HashCache:
    """Stat-keyed cache of file digests, stored in kitctl's cache dir (never in agent config)."""

    VERSION = 1

    def __init__(self, location: Path | None = None) -> None:
        self.location = location
        self._entries: dict[str, list[object]] = {}
        self.memo: dict[str, dict[str, str]] = {}  # digest -> derived metadata (frontmatter)
        self._dirty = False
        if location and location.is_file():
            try:
                data = json.loads(location.read_text("utf-8"))
                if data.get("version") == self.VERSION and data.get("scheme") == HASH_SCHEME:
                    self._entries = data["entries"]
                    self.memo = data.get("memo", {})
            except (OSError, ValueError, KeyError, TypeError):
                self._entries = {}

    def digest(self, path: Path, stat: os.stat_result | None = None) -> str:
        stat = stat or path.stat()
        key = [stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0)]
        cached = self._entries.get(str(path))
        if cached and cached[:3] == key:
            return str(cached[3])
        value = file_digest(path)
        self._entries[str(path)] = [*key, value]
        self._dirty = True
        return value

    def remember(self, digest: str, value: dict[str, str]) -> None:
        if self.memo.get(digest) != value:
            self.memo[digest] = value
            self._dirty = True

    def save(self) -> None:
        if not (self.location and self._dirty):
            return
        self.location.parent.mkdir(parents=True, exist_ok=True)
        staging = self.location.with_suffix(".tmp")
        live = {str(entry[3]) for entry in self._entries.values()}
        memo = {digest: value for digest, value in self.memo.items() if digest in live}
        payload = {
            "version": self.VERSION,
            "scheme": HASH_SCHEME,
            "entries": self._entries,
            "memo": memo,
        }
        staging.write_text(json.dumps(payload), "utf-8")
        os.replace(staging, self.location)
        self._dirty = False
