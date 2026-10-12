"""Usage mining: count skill, agent, command, and MCP invocations from session logs.

Only identities (names), timestamps, and working directories are extracted; prompts,
outputs, and tool arguments are never stored or printed.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Event:
    kind: str  # "skill" | "agent" | "command" | "mcp_server"
    name: str
    timestamp: str  # ISO 8601 as written by the harness ("" if absent)
    cwd: str  # "" if unknown
    detail: str = ""  # e.g. MCP tool name; identity only


@dataclass
class UsageRecord:
    harness: str
    kind: str
    name: str
    repo: str | None
    calls: int
    sessions: int
    first_used: str
    last_used: str

    def to_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "kind": self.kind,
            "name": self.name,
            "repo": self.repo,
            "calls": self.calls,
            "sessions": self.sessions,
            "first_used": self.first_used,
            "last_used": self.last_used,
        }


def text(value: object) -> str:
    """``value`` if it is a string, else ""."""
    return value if isinstance(value, str) else ""


def mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(path: str) -> str:
    text = unicodedata.normalize("NFC", path.replace("\\", "/")).rstrip("/")
    # os.path.normcase would turn "/" back into "\\" on Windows; fold case only.
    return text.casefold() if os.name == "nt" else text


def attribute(cwd: str, repos: Iterable[Path]) -> str | None:
    """The repository containing ``cwd`` (longest prefix), compared NFC- and case-normalized."""
    if not cwd:
        return None
    target = _norm(cwd)
    folded = target.casefold()
    best: tuple[int, str] | None = None
    for repo in repos:
        # Logs record the logical cwd; match the repo as given and with links resolved.
        for spelling in {str(repo), os.path.realpath(repo)}:
            candidate = _norm(spelling)
            for left, right in ((target, candidate), (folded, candidate.casefold())):
                if left == right or left.startswith(right + "/"):
                    if best is None or len(candidate) > best[0]:
                        best = (len(candidate), str(repo))
                    break
    return best[1] if best else None
