"""Codex session-log adapter (logs are documented as not a stable interface).

Reads ``$CODEX_HOME/sessions/**/*.jsonl`` and ``archived_sessions``. Shapes observed on
Codex 0.153.4 (macOS, 2026-10-12): records ``{type, timestamp, payload, ...}``;
``session_meta``/``turn_context`` payloads carry ``cwd``; MCP calls complete as
``event_msg`` with ``payload.type == "item_completed"`` and ``payload.item`` of type
``McpToolCall`` (``id``, ``server``, ``tool``). Namespaced ``function_call`` records are
tallied but not counted until their ids are confirmed to match ``McpToolCall`` items, so
nothing is double counted. Unknown shapes are counted, never fatal.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kitctl.usage import Event, mapping, text
from kitctl.usage.cache import UnknownShape

HARNESS = "codex"
ADAPTER_VERSION = 2
VERIFIED_VERSIONS: tuple[str, ...] = ("0.153.4",)  # partially: MCP calls only
KNOWN_TYPES = frozenset(
    {
        "session_meta",
        "response_item",
        "event_msg",
        "turn_context",
        "compacted",
        "message",
        "world_state",
        "token_usage_record",
        "retained_context",
        "inter_agent_communication_metadata",
    }
)
KNOWN_ITEMS = frozenset(
    {
        "McpToolCall",
        "CommandExecution",
        "FileChange",
        "AgentMessage",
        "Reasoning",
        "UserMessage",
        "SubAgentActivity",
        "CollabAgentToolCall",
        "ContextCompaction",
        "ImageView",
        "WebSearch",
        "TodoList",
    }
)
MAX_IDS = 50_000  # per-file dedupe memory bound


def files(codex_home: Path) -> Iterator[Path]:
    for folder in ("sessions", "archived_sessions"):
        root = codex_home / folder
        if root.is_dir():
            yield from sorted(root.rglob("*.jsonl"))


def _bump(state: dict[str, Any], key: str) -> None:
    state[key] = int(state.get(key, 0)) + 1


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
    if kind == "event_msg" and inner == "item_completed":
        item = mapping(payload.get("item"))
        item_type = text(item.get("type"))
        if item_type != "McpToolCall":
            if item_type not in KNOWN_ITEMS:
                _bump(state, "unrecognized_items")
            return []
        server = text(item.get("server"))
        ident = text(item.get("id"))
        seen: list[str] = state.setdefault("mcp_ids", [])
        if not server or (ident and ident in seen):
            return []
        if ident and len(seen) < MAX_IDS:
            seen.append(ident)
        return [Event("mcp_server", server, timestamp, cwd, text(item.get("tool")))]
    if inner in {"mcp_tool_call_begin", "mcp_tool_call"}:  # older Codex versions
        invocation = mapping(payload.get("invocation")) or payload
        server = text(invocation.get("server"))
        tool = text(invocation.get("tool"))
        return [Event("mcp_server", server, timestamp, cwd, tool)] if server else []
    if inner in {"function_call", "custom_tool_call", "tool_call"}:
        if text(payload.get("namespace")):
            _bump(state, "namespaced_calls")
        return []
    return []
