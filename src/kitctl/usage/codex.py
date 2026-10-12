"""Codex session-log adapter (best effort; logs are not a stable interface).

Reads ``$CODEX_HOME/sessions/**/*.jsonl``. Recognized: ``session_meta`` (cwd) and
function/tool-call records carrying a name; ``mcp__server__tool`` and
``mcp_tool_call`` shapes count as MCP use. Anything else is counted, never fatal.
Unverified locally; see docs/followups.md.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kitctl.usage import Event, mapping, text
from kitctl.usage.cache import UnknownShape

HARNESS = "codex"
ADAPTER_VERSION = 1
VERIFIED_VERSIONS: tuple[str, ...] = ()
KNOWN_TYPES = frozenset(
    {"session_meta", "response_item", "event_msg", "turn_context", "compacted", "message"}
)


def files(codex_home: Path) -> Iterator[Path]:
    root = codex_home / "sessions"
    if root.is_dir():
        yield from sorted(root.rglob("*.jsonl"))


def parse(line: bytes, state: dict[str, Any]) -> list[Event]:
    record = json.loads(line)
    if not isinstance(record, dict):
        raise UnknownShape
    kind = text(record.get("type"))
    payload = mapping(record.get("payload")) or record
    timestamp = text(record.get("timestamp"))
    if kind in {"session_meta", "turn_context"}:
        if text(payload.get("cwd")):
            state["cwd"] = text(payload.get("cwd"))
        return []
    if kind not in KNOWN_TYPES:
        raise UnknownShape
    cwd = text(state.get("cwd"))
    inner = text(payload.get("type"))
    if inner in {"mcp_tool_call_begin", "mcp_tool_call"}:
        invocation = mapping(payload.get("invocation")) or payload
        server = text(invocation.get("server"))
        tool = text(invocation.get("tool"))
        return [Event("mcp_server", server, timestamp, cwd, tool)] if server else []
    if inner in {"function_call", "custom_tool_call", "tool_call"}:
        name = text(payload.get("name"))
        if name.startswith("mcp__"):
            name = name[5:]
        server, separator, tool = name.partition("__")
        if separator and server and not server.startswith("_"):
            return [Event("mcp_server", server, timestamp, cwd, tool)]
    return []
