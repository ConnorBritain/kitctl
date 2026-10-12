"""Minimal TOML writer for kitctl's own documents (config, triage, primitive metadata).

Supports scalars (str, int, bool), lists of strings, one level of tables, and arrays of
tables. Reading always uses stdlib ``tomllib``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def string(value: str) -> str:
    escaped = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            escaped.append(f"\\u{ord(char):04X}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'


def key(name: str) -> str:
    return name if _BARE_KEY.match(name) else string(name)


def value(item: Any) -> str:
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, str):
        return string(item)
    if isinstance(item, list) and all(isinstance(element, str) for element in item):
        return "[" + ", ".join(string(element) for element in item) + "]"
    raise TypeError(f"Unsupported TOML value: {item!r}")


def _pairs(table: Mapping[str, Any]) -> list[str]:
    return [f"{key(name)} = {value(item)}" for name, item in table.items() if item is not None]


def dumps(document: Mapping[str, Any], header: str = "") -> str:
    """Scalars first, then ``[table]`` sections, then ``[[array]]`` tables."""
    lines = [f"# {line}" for line in header.splitlines()] if header else []
    scalars = {
        k: v
        for k, v in document.items()
        if not isinstance(v, (Mapping, list))
        or (isinstance(v, list) and all(isinstance(e, str) for e in v))
    }
    lines += _pairs(scalars)
    for name, item in document.items():
        if isinstance(item, Mapping):
            lines += ["", f"[{key(name)}]", *_pairs(item)]
    for name, item in document.items():
        if isinstance(item, list) and item and all(isinstance(e, Mapping) for e in item):
            for row in item:
                lines += ["", f"[[{key(name)}]]", *_pairs(row)]
    return "\n".join(lines) + "\n"
