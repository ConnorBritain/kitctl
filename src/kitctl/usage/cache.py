"""Incremental, bounded-memory scanning of append-only JSONL session logs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kitctl.usage import Event

MAX_LINE = 1 << 20  # longer lines are skipped (counted), never buffered whole
CACHE_VERSION = 1

Parser = Callable[[bytes, dict[str, Any]], list[Event]]


class UnknownShape(Exception):
    """Raised by a parser for a well-formed line it does not understand."""


@dataclass
class ScanStats:
    files: int = 0
    files_read: int = 0
    bytes_read: int = 0
    lines: int = 0
    malformed: int = 0
    unknown_shape: int = 0
    oversized: int = 0


@dataclass
class FileState:
    size: int
    mtime_ns: int
    inode: int
    offset: int
    state: dict[str, Any] = field(default_factory=dict)
    # key "kind\x1fname\x1fcwd\x1fday" -> [count, first_ts, last_ts]
    agg: dict[str, list[Any]] = field(default_factory=dict)
    malformed: int = 0
    unknown_shape: int = 0


def _key(event: Event) -> str:
    return "\x1f".join((event.kind, event.name, event.cwd, event.timestamp[:10]))


class UsageCache:
    def __init__(self, location: Path | None, adapter: str, adapter_version: int) -> None:
        self.location = location
        self.stamp = f"{adapter}:{adapter_version}:{CACHE_VERSION}"
        self.files: dict[str, FileState] = {}
        if location and location.is_file():
            try:
                data = json.loads(location.read_text("utf-8"))
                if data.get("stamp") == self.stamp:
                    self.files = {path: FileState(**state) for path, state in data["files"].items()}
            except (OSError, ValueError, KeyError, TypeError):
                self.files = {}

    def save(self) -> None:
        if not self.location:
            return
        self.location.parent.mkdir(parents=True, exist_ok=True)
        staging = self.location.with_suffix(".tmp")
        payload = {
            "stamp": self.stamp,
            "files": {path: state.__dict__ for path, state in self.files.items()},
        }
        staging.write_text(json.dumps(payload, separators=(",", ":")), "utf-8")
        os.replace(staging, self.location)

    def scan(self, paths: Iterable[Path], parse: Parser) -> ScanStats:
        stats = ScanStats()
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            seen.add(key)
            stats.files += 1
            try:
                info = path.stat()
            except OSError:
                continue
            inode = getattr(info, "st_ino", 0) or 0
            previous = self.files.get(key)
            if (
                previous
                and previous.size == info.st_size
                and previous.mtime_ns == info.st_mtime_ns
                and previous.inode == inode
            ):
                continue
            if previous and previous.inode == inode and info.st_size >= previous.offset:
                state = previous
            else:
                state = FileState(info.st_size, info.st_mtime_ns, inode, 0)
            try:
                self._read(path, state, parse, stats)
            except OSError:
                continue
            state.size, state.mtime_ns, state.inode = info.st_size, info.st_mtime_ns, inode
            self.files[key] = state
        for stale in set(self.files) - seen:
            del self.files[stale]
        return stats

    def _read(self, path: Path, state: FileState, parse: Parser, stats: ScanStats) -> None:
        stats.files_read += 1
        with path.open("rb") as handle:
            handle.seek(state.offset)
            offset = state.offset
            skipping = False
            while True:
                line = handle.readline(MAX_LINE)
                if not line:
                    break
                complete = line.endswith(b"\n")
                if not complete and len(line) < MAX_LINE:
                    break  # partial trailing line: leave it for the next run
                offset += len(line)
                stats.bytes_read += len(line)
                if skipping:
                    skipping = not complete
                    continue
                if not complete:
                    stats.oversized += 1
                    skipping = True
                    continue
                stats.lines += 1
                try:
                    events = parse(line, state.state)
                except UnknownShape:
                    state.unknown_shape += 1
                    stats.unknown_shape += 1
                    continue
                except (ValueError, TypeError, AttributeError, KeyError):
                    state.malformed += 1
                    stats.malformed += 1
                    continue
                for event in events:
                    slot = state.agg.setdefault(_key(event), [0, event.timestamp, event.timestamp])
                    slot[0] += 1
                    if event.timestamp:
                        if not slot[1] or event.timestamp < slot[1]:
                            slot[1] = event.timestamp
                        if event.timestamp > slot[2]:
                            slot[2] = event.timestamp
            state.offset = offset

    def totals(self) -> tuple[int, int]:
        return (
            sum(state.malformed for state in self.files.values()),
            sum(state.unknown_shape for state in self.files.values()),
        )
