"""Claude Code transcript adapter (``$CLAUDE_CONFIG_DIR/projects/**/*.jsonl``).

Verified against Claude Code 2.1.296 transcripts: assistant ``tool_use`` blocks name
``Skill`` (input.skill), ``Agent``/``Task`` (input.subagent_type), and
``mcp__<server>__<tool>``; user text carries ``<command-name>/x</command-name>``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kitctl.usage import Event, mapping, text
from kitctl.usage.cache import UnknownShape

HARNESS = "claude"
ADAPTER_VERSION = 1
VERIFIED_VERSIONS = ("2.1.296",)
_COMMAND = re.compile(r"<command-name>/?([^<\s]{1,200})</command-name>")


def files(config_dir: Path) -> Iterator[Path]:
    root = config_dir / "projects"
    if root.is_dir():
        yield from sorted(root.rglob("*.jsonl"))


def _text(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    yield text


def parse(line: bytes, state: dict[str, Any]) -> list[Event]:
    if b'"tool_use"' not in line and b"command-name" not in line:
        return []
    record = json.loads(line)
    if not isinstance(record, dict) or "type" not in record:
        raise UnknownShape
    timestamp = text(record.get("timestamp"))
    cwd = text(record.get("cwd"))
    content = mapping(record.get("message")).get("content")
    events: list[Event] = []
    if record["type"] == "assistant" and isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = text(block.get("name"))
            data = mapping(block.get("input"))
            if name == "Skill" and text(data.get("skill")):
                events.append(Event("skill", text(data.get("skill")), timestamp, cwd))
            elif name in {"Agent", "Task"}:
                agent = text(data.get("subagent_type")) or "default"
                events.append(Event("agent", agent, timestamp, cwd))
            elif name.startswith("mcp__"):
                _, _, rest = name.partition("mcp__")
                server, _, tool = rest.partition("__")
                if server:
                    events.append(Event("mcp_server", server, timestamp, cwd, tool))
    elif record["type"] == "user":
        for chunk in _text(content):
            for match in _COMMAND.finditer(chunk):
                events.append(Event("command", match.group(1), timestamp, cwd))
    return events
