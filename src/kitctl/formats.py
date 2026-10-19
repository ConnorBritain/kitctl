"""Precise, style-preserving edits to native config files.

Each format exposes the same small surface: parse text into a document, read,
insert and remove one owned entry by key, and render the document back. JSON
keeps the file's indentation, key order, final newline and line endings; TOML
goes through tomlkit so comments and unrelated tables survive; Markdown blocks
are delimited by kit markers. Every insertion reports the parents it created so
removal can take back exactly what Kit added and nothing more.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import tomlkit
from tomlkit.items import Comment, Table

MISSING: Any = object()


class FormatError(ValueError):
    """The file cannot be edited safely (malformed, wrong type at a key, duplicate marker)."""


@dataclass(frozen=True)
class Style:
    crlf: bool
    final_newline: bool
    indent: str | int | None = 2


def detect_style(text: str) -> Style:
    crlf = "\r\n" in text
    final = text.endswith("\n") or text == ""
    indent: str | int | None = 2
    match = re.search(r"\n([ \t]+)\S", text.replace("\r\n", "\n"))
    if match:
        whitespace = match.group(1)
        indent = "\t" if whitespace.startswith("\t") else len(whitespace)
    return Style(crlf, final, indent)


def _endings(text: str, style: Style) -> str:
    if style.final_newline and not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n") if style.crlf else text


# JSON ----------------------------------------------------------------------------------------


def _pointer(key: str) -> list[str]:
    if not key.startswith("/"):
        raise FormatError(f"not a JSON pointer: {key!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in key[1:].split("/")]


def json_load(text: str) -> Any:
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as error:
        raise FormatError(f"malformed JSON: {error}") from error


def json_dump(doc: Any, style: Style) -> str:
    return _endings(json.dumps(doc, indent=style.indent, ensure_ascii=False), style)


def json_get(doc: Any, key: str) -> Any:
    parts = _pointer(key)
    node = doc
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    last = parts[-1]
    if last == "-":
        return node if isinstance(node, list) else MISSING
    if not isinstance(node, dict):
        return MISSING
    return node.get(last, MISSING)


def json_set(doc: Any, key: str, value: Any) -> list[str]:
    """Insert value; return the pointers of containers created on the way."""
    parts = _pointer(key)
    created: list[str] = []
    node = doc
    path = ""
    for index, part in enumerate(parts[:-1]):
        if not isinstance(node, dict):
            raise FormatError(f"{path or '/'} is not an object")
        path += "/" + part.replace("~", "~0").replace("/", "~1")
        if part not in node:
            node[part] = [] if parts[index + 1] == "-" else {}
            created.append(path)
        node = node[part]
    last = parts[-1]
    if last == "-":
        if not isinstance(node, list):
            raise FormatError(f"{path} is not an array")
        if value not in node:
            node.append(value)
        return created
    if not isinstance(node, dict):
        raise FormatError(f"{path or '/'} is not an object")
    node[last] = value
    return created


def json_delete(doc: Any, key: str, value: Any, created: list[str]) -> None:
    parts = _pointer(key)
    node = doc
    chain = [doc]
    for part in parts[:-1]:
        node = node[part]
        chain.append(node)
    if parts[-1] == "-":
        node.remove(value)
    else:
        del node[parts[-1]]
    # Remove containers Kit created, innermost first, but only while they are empty.
    for depth in range(len(parts) - 1, 0, -1):
        pointer = "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts[:depth])
        container = chain[depth]
        if pointer in created and not container:
            del chain[depth - 1][parts[depth - 1]]
        else:
            break


# TOML ----------------------------------------------------------------------------------------


def toml_key(key: str) -> list[str]:
    """Split a dotted TOML key, honouring double-quoted parts."""
    if not re.fullmatch(r'(?:"[^"\\]*"|[A-Za-z0-9_-]+)(?:\.(?:"[^"\\]*"|[A-Za-z0-9_-]+))*', key):
        raise FormatError(f"cannot parse TOML key {key!r}")
    return [quoted or bare for quoted, bare in re.findall(r'"([^"\\]*)"|([A-Za-z0-9_-]+)', key)]


def toml_join(parts: list[str]) -> str:
    return ".".join(p if re.fullmatch(r"[A-Za-z0-9_-]+", p) else f'"{p}"' for p in parts)


def toml_load(text: str) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except Exception as error:  # tomlkit raises several parse error types
        raise FormatError(f"malformed TOML: {error}") from error


def toml_dump(doc: tomlkit.TOMLDocument, style: Style) -> str:
    text = tomlkit.dumps(doc).replace("\r\n", "\n")
    return _endings(text, style) if text else text


def _plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def toml_get(doc: tomlkit.TOMLDocument, key: str) -> Any:
    node: Any = doc
    for part in toml_key(key):
        if not hasattr(node, "get") or part not in node:
            return MISSING
        node = node[part]
    return _plain(node)


def _table(value: dict[str, Any]) -> Table:
    table = tomlkit.table()
    for name, item in value.items():
        table.add(name, item)
    return table


def toml_set(doc: tomlkit.TOMLDocument, key: str, value: Any) -> list[str]:
    parts = toml_key(key)
    created: list[str] = []
    node: Any = doc
    for depth, part in enumerate(parts[:-1]):
        if part not in node:
            holds_scalar = depth == len(parts) - 2 and not isinstance(value, dict)
            # Tables that only hold tables stay implicit, so no empty [header] lines appear.
            node.add(part, tomlkit.table(is_super_table=not holds_scalar))
            created.append(toml_join(parts[: depth + 1]))
        node = node[part]
        if not isinstance(node, Table | tomlkit.TOMLDocument):
            raise FormatError(f"{'.'.join(parts[: depth + 1])} is not a table")
    last = parts[-1]
    node[last] = _table(value) if isinstance(value, dict) else value
    return created


def _has_comment(item: Any) -> bool:
    if isinstance(item, Table):
        return any(
            isinstance(value, Comment) or _has_comment(value) for _, value in item.value.body
        )
    return False


def toml_delete(doc: tomlkit.TOMLDocument, key: str, created: list[str]) -> None:
    parts = toml_key(key)
    chain: list[Any] = [doc]
    for part in parts[:-1]:
        chain.append(chain[-1][part])
    # tomlkit keeps a comment written before the next [header] inside the previous table;
    # deleting that table would drop the user's comment, so refuse instead.
    if _has_comment(chain[-1].item(parts[-1]) if hasattr(chain[-1], "item") else None):
        raise FormatError(f"a comment sits inside Kit-owned {key}; move it, then re-run")
    del chain[-1][parts[-1]]
    for depth in range(len(parts) - 1, 0, -1):
        if toml_join(parts[:depth]) in created and len(chain[depth]) == 0:
            if _has_comment(chain[depth]):
                raise FormatError(
                    f"a comment sits inside Kit-owned {toml_join(parts[:depth])}; move it, "
                    "then re-run"
                )
            del chain[depth - 1][parts[depth - 1]]
        else:
            break


# Markdown managed blocks -----------------------------------------------------------------------


def block_text(marker: str, body: str) -> str:
    return (
        f"<!-- {marker.replace(':', ':begin ', 1)} -->\n{body.strip()}\n"
        f"<!-- {marker.replace(':', ':end ', 1)} -->\n"
    )


def _block_span(text: str, marker: str) -> tuple[int, int] | None:
    name = marker.split(":", 1)[1]
    begin = re.compile(rf"<!--\s*kit:begin\s+{re.escape(name)}\s*-->")
    end = re.compile(rf"<!--\s*kit:end\s+{re.escape(name)}\s*-->\r?\n?")
    starts = list(begin.finditer(text))
    if not starts:
        return None
    if len(starts) > 1:
        raise FormatError(f"duplicate {marker} markers")
    closing = end.search(text, starts[0].end())
    if closing is None:
        raise FormatError(f"{marker} block is never closed")
    return starts[0].start(), closing.end()


def block_get(text: str, marker: str) -> Any:
    span = _block_span(text.replace("\r\n", "\n"), marker)
    if span is None:
        return MISSING
    block = text.replace("\r\n", "\n")[span[0] : span[1]]
    return block.split("-->\n", 1)[1].rsplit("<!--", 1)[0].strip()


def block_insert(text: str, marker: str, body: str) -> tuple[str, str]:
    """Append the block; return (new text, the separator that was added before it)."""
    style = detect_style(text)
    plain = text.replace("\r\n", "\n")
    if _block_span(plain, marker) is not None:
        raise FormatError(f"{marker} block already present")
    separator = "" if not plain else ("\n" if plain.endswith("\n") else "\n\n")
    result = plain + separator + block_text(marker, body)
    return (result.replace("\n", "\r\n") if style.crlf else result), separator


def block_remove(text: str, marker: str, separator: str) -> str:
    style = detect_style(text)
    plain = text.replace("\r\n", "\n")
    span = _block_span(plain, marker)
    if span is None:
        raise FormatError(f"{marker} block is missing")
    start, end = span
    if separator and plain[start - len(separator) : start] == separator:
        start -= len(separator)
    result = plain[:start] + plain[end:]
    return result.replace("\n", "\r\n") if style.crlf else result


def strip_markers(text: str, name: str) -> str:
    """Eject: keep a block's text, drop its kit markers."""
    pattern = re.compile(rf"<!--\s*kit:(?:begin|end)\s+{re.escape(name)}\s*-->\r?\n?")
    return pattern.sub("", text)
