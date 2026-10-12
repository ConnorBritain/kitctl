"""The write engine: plan effects against the ownership ledger, show the diff, apply it.

* **Plan** (pure): compare desired effects with what kitctl owns (the ledger) and what is on
  disk. Each owned insertion is added, updated, released, or removed; anything on disk
  that kitctl doesn't own, or owned content someone changed, is a :class:`Conflict`, never
  a silent takeover.
* **Apply**: under a lock, re-check every file's preimage, stage new bytes beside each
  target, journal the intent, swap files in with ``os.replace``, then save the ledger and
  ``kit.lock``. A crash leaves a journal; the next run rolls forward (or ``--rollback``
  restores the backups). Multiple renames are not one atomic action; the journal makes
  them recoverable.

The ledger (``ledger.json`` in kitctl's state dir) is local and may hold preimages so purge
can restore them; ``kit.lock`` in a repository holds hashes only.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tomllib
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kitctl import fs
from kitctl._vendor import tomlkit
from kitctl._vendor.tomlkit.items import InlineTable
from kitctl.compile import Effect, element_id, source_files
from kitctl.paths import Paths
from kitctl.safemeta import canonical_hash
from kitctl.secretscan import find, redact

SCHEMA = 1
COMPILER_VERSION = 1
LOCK_FILE = "kit.lock"
BEGIN = "<!-- kitctl:begin {} -->"
END = "<!-- kitctl:end {} -->"


class EngineError(RuntimeError):
    pass


class RecoveryNeeded(EngineError):
    pass


def vhash(value: Any) -> str:
    return canonical_hash(value)


def bhash(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- ledger ---------------------------------------------------------------------------------


class Ledger:
    def __init__(self, paths: Paths) -> None:
        self.path = paths.kit_state_dir / "ledger.json"
        self.records: dict[str, dict[str, Any]] = {}
        # Per shared file: hash of its bytes before kitctl first changed it ("original",
        # None if absent, "stale" once someone else edited it) and after kitctl's last write.
        self.files: dict[str, dict[str, Any]] = {}
        self.store = paths.kit_state_dir / "preimages"
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError) as error:
                raise EngineError(
                    f"Ledger {self.path} is unreadable ({error}). kitctl won't guess what it "
                    "owns; restore the file from a backup or remove it and re-run apply "
                    "(existing identical entries are re-adopted, others become conflicts)."
                ) from error
            if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
                raise EngineError(f"Ledger {self.path} has an unsupported schema")
            self.records = dict(data.get("effects", {}))
            self.files = dict(data.get("files", {}))

    def owned(self, owner: str) -> dict[str, dict[str, Any]]:
        return {k: r for k, r in self.records.items() if r["owner"] == owner}

    def owners(self) -> list[str]:
        return sorted({r["owner"] for r in self.records.values()})

    def text(self, records: dict[str, dict[str, Any]], files: dict[str, Any] | None = None) -> str:
        targets = {r["target"] for r in records.values()}
        kept = {k: v for k, v in (self.files if files is None else files).items() if k in targets}
        document = {"schema_version": SCHEMA, "effects": records, "files": kept}
        return json.dumps(document, indent=2, sort_keys=True)

    def preimage(self, digest: str) -> bytes | None:
        try:
            return (self.store / digest).read_bytes()
        except OSError:
            return None


# -- document access ------------------------------------------------------------------------


@dataclass
class Style:
    indent: int | str = 2
    newline: str = "\n"
    trailing: bool = True


def _style(text: str) -> Style:
    match = re.search(r"^[{\[]\r?\n([ \t]+)\S", text)
    indent: int | str = 2
    if match:
        indent = match.group(1) if "\t" in match.group(1) else len(match.group(1))
    return Style(indent, "\r\n" if "\r\n" in text else "\n", text.endswith("\n") or not text)


def _dump_json(document: Any, style: Style) -> bytes:
    text = json.dumps(document, indent=style.indent, ensure_ascii=False)
    text += "\n" if style.trailing else ""
    return text.replace("\n", style.newline).encode("utf-8")


class Document:
    """One target file loaded for editing (JSON, TOML, Markdown with blocks)."""

    def __init__(self, path: Path, fmt: str) -> None:
        self.path = path
        self.fmt = fmt
        if path.is_symlink():
            raise EngineError(f"{path} is a symlink; kitctl won't write through links")
        if path.exists() and not path.is_file():
            raise EngineError(f"{path} exists but isn't a regular file")
        self.before: bytes | None = path.read_bytes() if path.is_file() else None
        text = self.before.decode("utf-8") if self.before is not None else ""
        if text.startswith("\ufeff"):
            raise EngineError(f"{path} starts with a byte-order mark; save it as plain UTF-8")
        self.style = _style(text)
        self._trim: int | None = None
        self.data: Any
        if fmt == "json":
            try:
                self.data = json.loads(text, object_pairs_hook=_unique) if text.strip() else {}
            except ValueError as error:
                raise EngineError(f"{path} is not valid JSON ({error}); fix it first") from error
            if not isinstance(self.data, dict):
                raise EngineError(f"{path} must hold a JSON object")
        elif fmt == "toml":
            try:
                self.data = tomlkit.parse(text)
            except Exception as error:  # tomlkit raises its own ParseError types
                raise EngineError(f"{path} is not valid TOML ({error}); fix it first") from error
        else:
            self.data = text

    # JSON / TOML keys and array elements --------------------------------------------------

    def _container(self, keys: tuple[str, ...], create: bool) -> tuple[Any, int]:
        node = self.data
        created = 0
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                if not create or not isinstance(node, dict):
                    return None, created
                node[key] = tomlkit.table(is_super_table=True) if self.fmt == "toml" else {}
                created += 1
            node = node[key]
        return node, created

    def get(self, op: str, identity: tuple[str, ...]) -> tuple[bool, Any]:
        if op == "block":
            found = self._block(identity[0])
            return (found is not None, found[2] if found else None)
        if op == "json-element":
            container, _ = self._container(identity[:-1], False)
            if isinstance(container, list):
                for element in container:
                    if element_id(_plain(element)) == identity[-1]:
                        return True, _plain(element)
            return False, None
        container, _ = self._container(identity[:-1], False)
        if isinstance(container, dict) and identity[-1] in container:
            return True, _plain(container[identity[-1]])
        return False, None

    def near(self, identity: tuple[str, ...], locator: list[str]) -> bool:
        """Is there an edited copy of a kitctl array element (same locator, other hash)?"""
        container, _ = self._container(identity[:-1], False)
        if not locator or not isinstance(container, list):
            return False
        return any(_locator(_plain(element)) == locator for element in container)

    def set(self, op: str, identity: tuple[str, ...], value: Any, old: Any = None) -> int:
        """Insert or replace; returns how many parent containers were created."""
        if op == "block":
            return self._set_block(identity[0], value)
        if op == "json-element":
            container, created = self._container(identity[:-2], True)
            assert isinstance(container, dict)
            array = container.get(identity[-2])
            if array is None:
                container[identity[-2]] = array = []
                created += 1
            if not isinstance(array, list):
                raise EngineError(f"{self.path}: {'.'.join(identity[:-1])} is not a list")
            if old is not None:
                for index, element in enumerate(array):
                    if element_id(_plain(element)) == element_id(old):
                        array[index] = value
                        return created
            array.append(value)
            return created
        container, created = self._container(identity[:-1], True)
        if not isinstance(container, dict):
            raise EngineError(f"{self.path}: {'.'.join(identity[:-1])} is not a table/object")
        if self.fmt == "toml" and isinstance(container, InlineTable):
            raise EngineError(
                f"{self.path}: {'.'.join(identity[:-1])} is an inline table; rewrite it as "
                f"[{'.'.join(identity[:-1])}.<name>] tables first"
            )
        container[identity[-1]] = _toml_value(value) if self.fmt == "toml" else value
        return created

    def delete(
        self,
        op: str,
        identity: tuple[str, ...],
        created: Collection[tuple[str, ...]],
        prefix: str = "",
    ) -> None:
        """Remove an entry, then any containers above it that kitctl created and left empty."""
        if op == "block":
            self._delete_block(identity[0], prefix)
            return
        container, _ = self._container(identity[:-1], False)
        if op == "json-element" and isinstance(container, list):
            for index, element in enumerate(container):
                if element_id(_plain(element)) == identity[-1]:
                    del container[index]
                    break
        elif isinstance(container, dict):
            container.pop(identity[-1], None)
        parents = identity[:-1]
        for depth in range(len(parents)):
            keys = parents[: len(parents) - depth]
            if keys not in created:
                break
            holder, _ = self._container(keys[:-1], False)
            node = holder.get(keys[-1]) if isinstance(holder, dict) else None
            if node is not None and len(node) == 0:
                holder.pop(keys[-1])
            else:
                break

    # Markdown blocks -----------------------------------------------------------------------

    def _block(self, name: str) -> tuple[int, int, str] | None:
        begin, end = BEGIN.format(name), END.format(name)
        text: str = self.data
        starts = [m.start() for m in re.finditer(re.escape(begin), text)]
        ends = [m.start() for m in re.finditer(re.escape(end), text)]
        if not starts and not ends:
            return None
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            raise EngineError(f"{self.path}: duplicate or unbalanced kitctl markers for {name}")
        body_start = text.index("\n", starts[0]) + 1
        stop = text.find("\n", ends[0])
        stop = len(text) if stop < 0 else stop + 1
        return starts[0], stop, text[body_start : ends[0]]

    def _set_block(self, name: str, body: str) -> int:
        block = f"{BEGIN.format(name)}\n{body}{END.format(name)}\n"
        found = self._block(name)
        text: str = self.data
        if found:
            self.data = text[: found[0]] + block + text[found[1] :]
        elif not text:
            self.data = block
        else:
            self.data = text + ("\n" if text.endswith("\n") else "\n\n") + block
        return 0

    def block_prefix(self) -> str:
        text: str = self.data
        return "" if not text else ("\n" if text.endswith("\n") else "\n\n")

    def _delete_block(self, name: str, prefix: str) -> None:
        found = self._block(name)
        if not found:
            return
        text: str = self.data
        start = found[0]
        if prefix and text[:start].endswith(prefix):
            start -= len(prefix)
        self.data = text[:start] + text[found[1] :]

    # Rendering -----------------------------------------------------------------------------

    def empty(self) -> bool:
        if self.fmt == "json":
            return not self.data
        if self.fmt == "toml":
            return not str(tomlkit.dumps(self.data)).strip()
        return not str(self.data).strip()

    def tail(self) -> int:
        """Trailing newlines in the TOML text now (tomlkit pads before appended tables)."""
        if self.fmt != "toml":
            return 0
        text = str(tomlkit.dumps(self.data))
        return len(text) - len(text.rstrip("\n"))

    def trim_tail(self, tail: int | None) -> None:
        if self.fmt == "toml" and tail:
            self._trim = tail if self._trim is None else min(self._trim, tail)

    def render(self) -> bytes:
        if self.fmt == "toml" and self._trim is not None:
            text = str(tomlkit.dumps(self.data))
            stripped = text.rstrip("\n")
            if stripped and len(text) - len(stripped) > self._trim:
                self.data = tomlkit.parse(stripped + "\n" * self._trim)
        if self.fmt == "json":
            return _dump_json(self.data, self.style)
        if self.fmt == "toml":
            return str(tomlkit.dumps(self.data)).encode("utf-8")
        return str(self.data).encode("utf-8")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON object hook: duplicate keys would silently lose data on rewrite."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def valid(fmt: str, data: bytes) -> str | None:
    """Why rendered bytes wouldn't load, or None."""
    try:
        text = data.decode("utf-8")
        if fmt == "json":
            json.loads(text)
        elif fmt == "toml":
            tomllib.loads(text)
    except (UnicodeDecodeError, ValueError) as error:
        return str(error)
    return None


def _plain(value: Any) -> Any:
    unwrap = getattr(value, "unwrap", None)
    return unwrap() if callable(unwrap) else value


def _toml_value(value: Any) -> Any:
    if isinstance(value, dict):
        table = tomlkit.table()
        for key, item in value.items():
            if isinstance(item, dict):
                inline = tomlkit.inline_table()
                inline.update(item)
                table[key] = inline
            else:
                table[key] = item
        return table
    return value


def _format(op: str) -> str:
    return {"json-key": "json", "json-element": "json", "toml-key": "toml", "block": "text"}[op]


def _tree_state(directory: Path) -> dict[str, str] | None:
    if directory.is_symlink():
        raise EngineError(f"{directory} is a symlink; kitctl won't write through links")
    if directory.exists() and not directory.is_dir():
        raise EngineError(f"{directory} exists but isn't a directory")
    if not directory.is_dir():
        return None
    return {rel: bhash(path.read_bytes()) or "" for rel, path in source_files(directory).items()}


# -- plan -----------------------------------------------------------------------------------


@dataclass
class Change:
    action: str  # add | update | remove | restore | adopt | release | missing
    item: str
    harness: str
    op: str
    target: Path
    identity: tuple[str, ...]
    before: Any = None
    after: Any = None


@dataclass
class Conflict:
    item: str
    target: Path
    identity: tuple[str, ...]
    reason: str


@dataclass
class FileWrite:
    path: Path
    before: bytes | None
    after: bytes | None  # None deletes the file
    mode: int | None = None


@dataclass
class Plan:
    owner: str
    changes: list[Change] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    writes: list[FileWrite] = field(default_factory=list)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)  # owner's ledger after
    remove_dirs: list[tuple[Path, Path]] = field(default_factory=list)  # (dir, a target)
    notes: list[str] = field(default_factory=list)
    files: dict[str, dict[str, Any] | None] = field(default_factory=dict)  # file baselines
    preserve: dict[str, bytes] = field(default_factory=dict)  # preimages to keep, by hash

    @property
    def empty(self) -> bool:
        return not self.writes and not any(c.action in ("adopt", "release") for c in self.changes)


def _record(effect: Effect, value_hash: str, **extra: Any) -> dict[str, Any]:
    return {
        "owner": effect.owner,
        "item": effect.item,
        "harness": effect.harness,
        "op": effect.op,
        "target": str(effect.target),
        "identity": list(effect.identity),
        "written_hash": value_hash,
        "applied_at": now(),
        **extra,
    }


def _locator(value: Any) -> list[str]:
    """What identifies a JSON array element even after someone edits it: hook commands,
    or the string itself (permission rules)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict) and isinstance(value.get("hooks"), list):
        return sorted(
            h["command"] for h in value["hooks"] if isinstance(h, dict) and "command" in h
        )
    return []


def _created_prefixes(record: dict[str, Any]) -> set[tuple[str, ...]]:
    """Container key paths kitctl created when it inserted ``record``."""
    parents = tuple(record["identity"][:-1])
    count = int(record.get("created_parents", 0))
    return {parents[: len(parents) - depth] for depth in range(min(count, len(parents)))}


def _files(effect: Effect) -> dict[str, Any]:
    """Tree records list their files so the guard hook needn't walk directories."""
    return {"files": sorted(effect.value)} if effect.op == "tree" else {}


def _effect_from(record: dict[str, Any]) -> Effect:
    return Effect(
        record["owner"],
        record["item"],
        record["harness"],
        record["op"],
        Path(record["target"]),
        tuple(record["identity"]),
        None,
    )


def _secretish(value: Any) -> bool:
    if isinstance(value, dict):
        env = value.get("env") or {}
        headers = value.get("headers") or value.get("http_headers") or {}
        literal = [
            v for v in [*env.values(), *headers.values()] if isinstance(v, str) and "${" not in v
        ]
        if literal:
            return True
    return bool(find(json.dumps(value, default=str)))


class Planner:
    def __init__(self, owner: str, effects: list[Effect], ledger: Ledger, replace: bool) -> None:
        self.plan = Plan(owner)
        self.ledger = ledger
        self.replace = replace
        self.desired = {e.key: e for e in effects}
        self.owned = ledger.owned(owner)
        self.others = {k: r for k, r in ledger.records.items() if r["owner"] != owner}
        self.created: set[Path] = set()

    def conflict(self, effect: Effect, reason: str) -> None:
        self.plan.conflicts.append(Conflict(effect.item, effect.target, effect.identity, reason))

    def run(self) -> Plan:
        groups: dict[tuple[Path, str], list[str]] = {}
        for key, effect in self.desired.items():
            groups.setdefault((effect.target, effect.op), []).append(key)
        for key, record in self.owned.items():
            groups.setdefault((Path(record["target"]), record["op"]), []).append(key)
        documents: dict[Path, Document] = {}
        for (target, op), keys in sorted(groups.items(), key=lambda g: (str(g[0][0]), g[0][1])):
            keys = sorted(set(keys))
            try:
                if op in ("file", "tree"):
                    for key in keys:
                        self.copy(key)
                    continue
                document = documents.get(target)
                if document is None:
                    document = documents[target] = Document(target, _format(op))
                for key in keys:
                    self.edit(document, key)
            except (EngineError, OSError, UnicodeDecodeError) as error:
                effect = self.desired.get(keys[0]) or _effect_from(self.owned[keys[0]])
                self.conflict(effect, str(error))
        for document in documents.values():
            self.finish(document)
        return self.plan

    def keep(self, key: str, record: dict[str, Any]) -> None:
        self.plan.records[key] = record

    def edit(self, document: Document, key: str) -> None:
        effect = self.desired.get(key)
        record = self.owned.get(key)
        if effect and key in self.others:
            self.conflict(effect, f"already owned by {self.others[key]['owner']}")
            return
        base = effect if effect is not None else _effect_from(self.owned[key])
        present, current = document.get(base.op, base.identity)
        current_hash = vhash(current) if present else None
        change = Change("", base.item, base.harness, base.op, base.target, base.identity, current)
        if effect and record:
            desired_hash = vhash(effect.value)
            extra: dict[str, Any] = {}
            if not present:
                if base.op == "json-element" and document.near(
                    base.identity, record.get("locator", [])
                ):
                    self.conflict(
                        effect,
                        "edited since kitctl wrote it (drift); revert the "
                        "edit or adopt the new version into the warehouse",
                    )
                    self.keep(key, record)
                    return
                change.action, change.after = "restore", effect.value
                document.set(effect.op, effect.identity, effect.value)
            elif current_hash == record["written_hash"]:
                if desired_hash == current_hash:
                    self.keep(key, record)
                    return
                change.action, change.after = "update", effect.value
                document.set(effect.op, effect.identity, effect.value, old=current)
                if record.get("preexisting"):
                    # The user's own entry, adopted unchanged; keep it so purge restores it.
                    extra = {"preexisting": False, "preimage_present": True, "preimage": current}
            else:
                self.conflict(
                    effect,
                    "changed since kitctl wrote it (drift); revert the edit or "
                    "adopt the new version into the warehouse",
                )
                self.keep(key, record)
                return
            self.keep(
                key,
                record
                | {"written_hash": desired_hash, "applied_at": now(), "item": effect.item}
                | {"locator": _locator(effect.value)}
                | extra,
            )
        elif effect:
            desired_hash = vhash(effect.value)
            if not present:
                prefix = document.block_prefix() if effect.op == "block" else ""
                tail = document.tail()
                created_file = document.before is None
                created = document.set(effect.op, effect.identity, effect.value)
                change.action, change.after = "add", effect.value
                dirs = _missing_dirs(document.path.parent) if created_file else []
                self.keep(
                    key,
                    _record(
                        effect,
                        desired_hash,
                        preexisting=False,
                        preimage_present=False,
                        created_parents=created,
                        created_file=created_file,
                        block_prefix=prefix,
                        created_dirs=dirs,
                        file_tail=tail,
                        locator=_locator(effect.value),
                    ),
                )
            elif current_hash == desired_hash:
                change.action = "adopt"
                self.keep(
                    key,
                    _record(
                        effect,
                        desired_hash,
                        preexisting=True,
                        preimage_present=True,
                        created_parents=0,
                        created_file=False,
                        locator=_locator(effect.value),
                    ),
                )
            elif self.replace and not _secretish(current):
                change.action, change.after = "update", effect.value
                document.set(effect.op, effect.identity, effect.value, old=current)
                self.keep(
                    key,
                    _record(
                        effect,
                        desired_hash,
                        preexisting=False,
                        preimage_present=True,
                        preimage=current,
                        created_parents=0,
                        created_file=False,
                        locator=_locator(effect.value),
                    ),
                )
            else:
                why = (
                    "holds what look like literal credentials, which kitctl won't overwrite or "
                    "keep as a preimage. Put them in environment variables, remove this entry "
                    "yourself (e.g. `claude mcp remove <name> -s user` or `codex mcp remove "
                    "<name>`), then re-run apply"
                    if _secretish(current)
                    else "exists with different content and isn't kitctl's (pass --replace to "
                    "take it over; purge then restores the original)"
                )
                self.conflict(effect, why)
                return
        else:
            assert record is not None
            if (
                not present
                and base.op == "json-element"
                and document.near(base.identity, record.get("locator", []))
            ):
                self.conflict(
                    base,
                    "edited since kitctl wrote it (drift); kitctl won't remove "
                    "it. Revert the edit, or remove it yourself and re-run",
                )
                self.keep(key, record)
                return
            if not present:
                change.action = "release"
                self.plan.notes.append(f"{base.item}: already removed from {base.target}")
            elif current_hash != record["written_hash"]:
                self.conflict(
                    base,
                    "changed since kitctl wrote it (drift); kitctl won't remove "
                    "it. Revert the edit, or remove it yourself and re-run",
                )
                self.keep(key, record)
                return
            elif record.get("preexisting"):
                change.action = "release"
            elif record.get("preimage_present"):
                change.action, change.after = "restore", record.get("preimage")
                document.set(base.op, base.identity, record.get("preimage"), old=current)
            else:
                change.action = "remove"
                document.trim_tail(record.get("file_tail"))
                made = {
                    prefix
                    for other in self.ledger.records.values()
                    if other["target"] == record["target"]
                    for prefix in _created_prefixes(other)
                }
                document.delete(base.op, base.identity, made, record.get("block_prefix", ""))
                if record.get("created_file"):
                    self.created.add(base.target)
        self.plan.changes.append(change)

    def finish(self, document: Document) -> None:
        path = str(document.path)
        after = document.render()
        state = self.ledger.files.get(path)
        remaining = any(r["target"] == path for r in self.plan.records.values()) or any(
            r["target"] == path for r in self.others.values()
        )
        unchanged = state is not None and bhash(document.before) == state["written"]
        if not remaining and unchanged and state is not None and state["original"] != "stale":
            # Everything kitctl put here is going and nobody else touched the file since:
            # put back the exact original bytes (or remove the file kitctl created).
            original = (
                None if state["original"] is None else self.ledger.preimage(state["original"])
            )
            if state["original"] is None or original is not None:
                if original != document.before:
                    mode = document.path.stat().st_mode & 0o7777 if document.before else None
                    self.plan.writes.append(
                        FileWrite(document.path, document.before, original, mode)
                    )
                if original is None:
                    self._remove_created_dirs(document.path)
                self.plan.files[path] = None
                return
        if document.path in self.created:
            still_owned = any(r["target"] == str(document.path) for r in self.plan.records.values())
            if document.empty() and not still_owned:
                self.plan.writes.append(FileWrite(document.path, document.before, None))
                self._remove_created_dirs(document.path)
                self.plan.files[path] = None
                return
        if after != document.before:
            problem = valid(document.fmt, after)
            if problem:
                self.plan.conflicts.append(
                    Conflict(
                        "(file)",
                        document.path,
                        (),
                        f"the edit would not parse ({problem}); nothing written",
                    )
                )
                return
            if remaining:
                baseline = (
                    bhash(document.before)
                    if state is None
                    else (state["original"] if unchanged else "stale")
                )
                if state is None and document.before is not None:
                    self.plan.preserve[bhash(document.before) or ""] = document.before
                self.plan.files[path] = {"original": baseline, "written": bhash(after)}
            else:
                self.plan.files[path] = None
            if document.before is not None and document.fmt == "json":
                original = document.before
                if (
                    _dump_json(json.loads(original.decode("utf-8") or "{}"), document.style)
                    != original
                ):
                    self.plan.notes.append(
                        f"{document.path}: formatting is normalized (indentation/spacing) when "
                        "rewritten; content outside kitctl's entries is unchanged"
                    )
            mode = document.path.stat().st_mode & 0o7777 if document.before is not None else None
            self.plan.writes.append(FileWrite(document.path, document.before, after, mode))

    def _remove_created_dirs(self, target: Path) -> None:
        dirs = {
            d
            for r in self.ledger.records.values()
            if r["target"] == str(target)
            for d in r.get("created_dirs", [])
        }
        for directory in sorted(dirs, key=lambda d: -len(Path(d).parts)):
            self.plan.remove_dirs.append((Path(directory), target))

    def copy(self, key: str) -> None:
        effect = self.desired.get(key)
        record = self.owned.get(key)
        base = effect if effect is not None else _effect_from(self.owned[key])
        target = base.target
        if effect and key in self.others:
            self.conflict(effect, f"already owned by {self.others[key]['owner']}")
            return
        if base.op == "tree":
            current: Any = _tree_state(target)
        else:
            if target.is_symlink():
                raise EngineError(f"{target} is a symlink; kitctl won't write through links")
            if target.exists() and not target.is_file():
                raise EngineError(f"{target} exists but isn't a regular file")
            current = bhash(target.read_bytes()) if target.is_file() else None
        present = current is not None
        change = Change("", base.item, base.harness, base.op, target, (), current)
        written = record["written_hash"] if record else None
        if effect:
            desired_hash = vhash(effect.value)
            current_hash = vhash(current) if present else None
            if present and record and current_hash != written:
                self.conflict(effect, "files changed since kitctl wrote them (drift)")
                self.keep(key, record)
                return
            if present and not record and current_hash != desired_hash:
                self.conflict(
                    effect,
                    "exists and isn't kitctl's; move it away or adopt it "
                    "into the warehouse, then re-run",
                )
                return
            if present and current_hash == desired_hash:
                if record:
                    self.keep(key, record)
                else:
                    change.action = "adopt"
                    self.plan.changes.append(change)
                    self.keep(
                        key,
                        _record(effect, desired_hash, preexisting=True, created_dirs=[])
                        | _files(effect),
                    )
                return
            change.action = "update" if present else ("restore" if record else "add")
            change.after = effect.value
            self.plan.changes.append(change)
            created_dirs = (
                record.get("created_dirs", [])
                if record
                else _missing_dirs(target if base.op == "tree" else target.parent)
            )
            extra: dict[str, Any] = {}
            if record and record.get("preexisting") and present:
                # Adopted user files are being replaced: keep their bytes so purge restores them.
                saved: dict[str, str] = {}
                originals = (
                    [(rel, target / rel) for rel in current]
                    if base.op == "tree"
                    else [("", target)]
                )
                for rel, path in originals:
                    data = path.read_bytes()
                    digest = bhash(data) or ""
                    self.plan.preserve[digest] = data
                    saved[rel] = digest
                extra = {"preimage_files": saved}
            self.write_copy(effect, current if present else None)
            self.keep(
                key,
                (record or {})
                | _record(effect, desired_hash, preexisting=False, created_dirs=created_dirs)
                | _files(effect)
                | extra,
            )
            return
        assert record is not None
        if not present:
            change.action = "release"
        elif vhash(current) != written:
            self.conflict(
                base, "files changed since kitctl wrote them (drift); kitctl won't delete them"
            )
            self.keep(key, record)
            return
        elif record.get("preexisting"):
            change.action = "release"
        elif record.get("preimage_files"):
            change.action = "restore"
            saved = record["preimage_files"]
            for rel, digest in saved.items():
                path = target / rel if rel else target
                data = self.ledger.preimage(digest)
                if data is None:
                    raise EngineError(
                        f"the saved original of {path} is missing from "
                        f"{self.ledger.store}; kitctl won't remove it"
                    )
                before = path.read_bytes() if path.is_file() else None
                if before != data:
                    self.plan.writes.append(FileWrite(path, before, data, None))
            if base.op == "tree":
                for rel in sorted(set(current) - set(saved)):
                    self.plan.writes.append(
                        FileWrite(target / rel, (target / rel).read_bytes(), None)
                    )
        else:
            change.action = "remove"
            files = [target / rel for rel in current] if base.op == "tree" else [target]
            for path in files:
                self.plan.writes.append(FileWrite(path, path.read_bytes(), None))
            anchor = files[0]
            dirs: list[Path] = []
            if base.op == "tree":
                subdirs = {(target / rel).parent for rel in current}
                for directory in sorted(subdirs, key=lambda d: -len(d.parts)):
                    if directory != target:
                        dirs.append(directory)
            for directory in [*dirs, *map(Path, reversed(record.get("created_dirs", [])))]:
                self.plan.remove_dirs.append((directory, anchor))
        self.plan.changes.append(change)

    def write_copy(self, effect: Effect, current: Any) -> None:
        assert effect.source is not None
        if effect.op == "file":
            data = effect.source.read_bytes()
            before = effect.target.read_bytes() if current is not None else None
            mode = effect.source.stat().st_mode & 0o777
            self.plan.writes.append(FileWrite(effect.target, before, data, mode))
            return
        sources = source_files(effect.source)
        old = current or {}
        for rel, path in sources.items():
            target = effect.target / rel
            data = path.read_bytes()
            if old.get(rel) == bhash(data):
                continue
            before = target.read_bytes() if rel in old else None
            self.plan.writes.append(FileWrite(target, before, data, path.stat().st_mode & 0o777))
        for rel in sorted(set(old) - set(sources)):
            target = effect.target / rel
            self.plan.writes.append(FileWrite(target, target.read_bytes(), None))
            for parent in target.parents:
                if parent == effect.target or not parent.is_relative_to(effect.target):
                    break
                if not any((effect.target / r).is_relative_to(parent) for r in sources):
                    self.plan.remove_dirs.append((parent, target))


def _missing_dirs(directory: Path) -> list[str]:
    missing = []
    for parent in [directory, *directory.parents]:
        if parent.exists():
            break
        missing.append(str(parent))
    return list(reversed(missing))


def plan(
    owner: str,
    effects: list[Effect],
    paths: Paths,
    replace: bool = False,
    lock: dict[str, Any] | None = None,
) -> Plan:
    """Compute the changes for ``owner``; ``lock`` adds a repository's kit.lock."""
    ledger = Ledger(paths)
    result = Planner(owner, effects, ledger, replace).run()
    if lock is not None and owner not in ("user",) and not owner.startswith("capture"):
        repo = Path(owner)
        target = repo / LOCK_FILE
        before = target.read_bytes() if target.is_file() else None
        after = None
        if result.records:
            after = (
                json.dumps(_lock(repo, result.records, lock), indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
        if after != before:
            result.writes.append(FileWrite(target, before, after))
    return result


def _lock(repo: Path, records: dict[str, dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    effects = []
    for record in records.values():
        target = Path(record["target"])
        try:
            shown = target.relative_to(repo).as_posix()
        except ValueError:
            shown = str(target)
        effects.append(
            {
                "item": record["item"],
                "harness": record["harness"],
                "op": record["op"],
                "target": shown,
                "identity": record["identity"],
                "written_hash": record["written_hash"],
            }
        )
    effects.sort(key=lambda e: (e["target"], e["op"], json.dumps(e["identity"])))
    return {
        "schema_version": SCHEMA,
        "compiler_version": COMPILER_VERSION,
        "warehouse_commit": meta.get("commit"),
        "loadouts": meta.get("loadouts", []),
        "items": sorted({r["item"] for r in records.values()}),
        "effects": effects,
    }


# -- diff -----------------------------------------------------------------------------------


def _short(value: Any) -> str:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= 160 else text[:157] + "..."


def render_plan(result: Plan, home: Path | None = None) -> str:
    def shown(path: Path) -> str:
        if home and path.is_relative_to(home):
            return "~/" + path.relative_to(home).as_posix()
        return str(path)

    lines: list[str] = []
    marks = {"add": "+", "update": "~", "remove": "-", "restore": "~", "adopt": "=", "release": "="}
    by_target: dict[Path, list[Change]] = {}
    for change in result.changes:
        by_target.setdefault(change.target, []).append(change)
    for target, changes in sorted(by_target.items(), key=lambda t: str(t[0])):
        lines.append(shown(target))
        for change in changes:
            where = ".".join(i for i in change.identity if not i.startswith("#"))
            label = f"{marks.get(change.action, '?')} {change.action:7} {change.item}"
            if change.op in ("json-key", "json-element", "toml-key"):
                detail = (
                    f" {where} = {_short(change.after)}"
                    if change.after is not None
                    else (f" {where}")
                )
            elif change.op == "tree":
                count = len(change.after or change.before or {})
                detail = f" ({count} files)"
            elif change.op == "block":
                detail = f" block {change.identity[0]}"
            else:
                detail = ""
            lines.append(f"  {label}{detail}")
    for write in result.writes:
        if write.path.name == LOCK_FILE:
            lines.append(f"{shown(write.path)}\n  ~ kit.lock (hashes only)")
            continue
        if write.path.suffix in (".md", ".rules") and write.after is not None:
            old = (write.before or b"").decode("utf-8", "replace").splitlines()
            new = write.after.decode("utf-8", "replace").splitlines()
            diff = list(
                difflib.unified_diff(
                    old, new, shown(write.path), shown(write.path), lineterm="", n=2
                )
            )
            if 0 < len(diff) <= 80:
                lines.extend("    " + line for line in diff)
    for conflict in result.conflicts:
        where = ".".join(i for i in conflict.identity if not i.startswith("#"))
        lines.append(
            f"CONFLICT {conflict.item} at {shown(conflict.target)} {where}: {conflict.reason}"
        )
    text, _ = redact("\n".join(lines))
    return text


# -- apply ----------------------------------------------------------------------------------


class _Lock:
    def __init__(self, paths: Paths) -> None:
        self.path = paths.kit_state_dir / "apply.lock"

    def __enter__(self) -> _Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fs.check(self.path)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise EngineError(
                f"Another kitctl apply is running (lock {self.path}). If none is, delete it."
            ) from error
        os.write(fd, f"{os.getpid()} {now()}\n".encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def _journal_path(paths: Paths) -> Path:
    return paths.kit_state_dir / "journal.json"


def _current(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def apply(result: Plan, paths: Paths, crash_after: int | None = None) -> None:
    """Apply a conflict-free plan. ``crash_after`` simulates an interruption (tests)."""
    if result.conflicts:
        raise EngineError(f"{len(result.conflicts)} conflicts; resolve them first")
    ledger_path = paths.kit_state_dir / "ledger.json"
    with _Lock(paths):
        recover(paths)
        ledger = Ledger(paths)
        records = {k: r for k, r in ledger.records.items() if r["owner"] != result.owner}
        records.update(result.records)
        files = dict(ledger.files)
        for path, state in result.files.items():
            if state is None:
                files.pop(path, None)
            else:
                files[path] = state
        for digest, data in result.preserve.items():
            if not (ledger.store / digest).is_file():
                fs.write_bytes(ledger.store / digest, data)
        for write in result.writes:
            if _current(write.path) != write.before:
                raise EngineError(f"{write.path} changed since the plan was made; re-run")
        token = uuid.uuid4().hex[:12]
        backups = paths.kit_state_dir / "backups" / token
        scope = [w.path for w in result.writes]
        with fs.agent_scope(scope):
            entries: list[dict[str, Any]] = []
            try:
                _stage(result, token, backups, entries)
            except BaseException:
                for entry in entries:
                    if entry["staged"]:
                        fs.agent_remove(Path(entry["staged"]))
                raise
            journal = {
                "schema_version": SCHEMA,
                "token": token,
                "owner": result.owner,
                "started_at": now(),
                "writes": entries,
                "ledger_before": ledger.text(ledger.records),
                "ledger_after": ledger.text(records, files),
                "remove_dirs": [[str(d), str(a)] for d, a in result.remove_dirs],
            }
            fs.write_atomic(_journal_path(paths), json.dumps(journal, indent=2), mode=0o600)
            for index, entry in enumerate(entries):
                if crash_after is not None and index >= crash_after:
                    raise KeyboardInterrupt("simulated interruption")
                _swap(entry)
            _finish(journal, paths, ledger_path)


def _stage(result: Plan, token: str, backups: Path, entries: list[dict[str, Any]]) -> None:
    for index, write in enumerate(result.writes):
        staged = None
        created: list[Path] = []
        if write.after is not None:
            created = fs.agent_mkdirs(write.path.parent, write.path)
            staged = str(fs.agent_stage(write.path, write.after, token, write.mode))
        backup = None
        mode = None
        if write.before is not None:
            backup = backups / str(index)
            fs.write_bytes(backup, write.before)
            mode = write.path.stat().st_mode & 0o7777
        entries.append(
            {
                "path": str(write.path),
                "before": bhash(write.before),
                "after": bhash(write.after),
                "staged": staged,
                "backup": str(backup) if backup else None,
                "mode": mode,
                "created_dirs": [str(d) for d in created],
            }
        )


def _swap(entry: dict[str, Any]) -> None:
    path = Path(entry["path"])
    if entry["staged"]:
        fs.agent_replace(Path(entry["staged"]), path)
    else:
        fs.agent_remove(path)


def _finish(journal: dict[str, Any], paths: Paths, ledger_path: Path) -> None:
    for directory, anchor in journal["remove_dirs"]:
        if Path(directory).is_dir():
            with fs.agent_scope([Path(anchor)]):
                fs.agent_rmdir(Path(directory), Path(anchor))
    fs.write_atomic(ledger_path, journal["ledger_after"], mode=0o600)
    _journal_path(paths).unlink(missing_ok=True)
    backups = paths.kit_state_dir / "backups" / journal["token"]
    if backups.is_dir():
        fs.check(backups)
        shutil.rmtree(backups)


def pending(paths: Paths) -> dict[str, Any] | None:
    path = _journal_path(paths)
    if not path.is_file():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    except ValueError as error:
        raise RecoveryNeeded(f"Journal {path} is unreadable: {error}") from error
    return data


def recover(paths: Paths, rollback: bool = False) -> str | None:
    """Finish (or with ``rollback``, undo) an interrupted apply. Returns a summary."""
    try:
        return _recover(paths, rollback)
    except OSError as error:
        hint = "" if rollback else " `kitctl recover --rollback` undoes the interrupted apply."
        raise RecoveryNeeded(f"Recovery failed: {error}.{hint}") from error


def _recover(paths: Paths, rollback: bool) -> str | None:
    journal = pending(paths)
    if journal is None:
        return None
    entries = journal["writes"]
    scope = [Path(e["path"]) for e in entries]
    problems = []
    for entry in entries:
        current = bhash(_current(Path(entry["path"])))
        if current not in (entry["before"], entry["after"]):
            problems.append(f"{entry['path']} was changed by something else")
        elif not rollback and current == entry["before"] and entry["staged"]:
            if not Path(entry["staged"]).is_file():
                problems.append(f"{entry['path']}: staged copy is missing")
        elif (
            rollback
            and current == entry["after"]
            and entry["before"]
            and not (entry["backup"] and Path(entry["backup"]).is_file())
        ):
            problems.append(f"{entry['path']}: backup is missing")
    if problems:
        raise RecoveryNeeded(
            "An interrupted apply can't be completed automatically (`kitctl recover "
            "--rollback` may undo it): "
            + "; ".join(problems)
            + f". Journal: {_journal_path(paths)}"
        )
    ledger_path = paths.kit_state_dir / "ledger.json"
    with fs.agent_scope(scope):
        for entry in entries:
            path = Path(entry["path"])
            current = bhash(_current(path))
            if rollback:
                if current == entry["after"] and current != entry["before"]:
                    if entry["backup"]:
                        staged = fs.agent_stage(
                            path,
                            Path(entry["backup"]).read_bytes(),
                            journal["token"] + "r",
                            entry.get("mode"),
                        )
                        fs.agent_replace(staged, path)
                    else:
                        fs.agent_remove(path)
                if entry["staged"] and Path(entry["staged"]).is_file():
                    fs.agent_remove(Path(entry["staged"]))
            elif current != entry["after"]:
                _swap(entry)
        if rollback:
            for entry in reversed(entries):
                for directory in reversed(entry.get("created_dirs", [])):
                    fs.agent_rmdir(Path(directory), Path(entry["path"]))
            fs.write_atomic(ledger_path, journal["ledger_before"], mode=0o600)
            _journal_path(paths).unlink(missing_ok=True)
            backups = paths.kit_state_dir / "backups" / journal["token"]
            if backups.is_dir():
                shutil.rmtree(backups)
            return f"Rolled back the interrupted apply for {journal['owner']}."
    _finish(journal, paths, ledger_path)
    return f"Completed the interrupted apply for {journal['owner']}."


# -- status ---------------------------------------------------------------------------------


def status(paths: Paths, owners: Iterable[str] | None = None) -> list[dict[str, Any]]:
    ledger = Ledger(paths)
    wanted = set(owners) if owners is not None else None
    rows = []
    documents: dict[tuple[Path, str], Document | None] = {}
    for record in sorted(ledger.records.values(), key=lambda r: (r["owner"], r["target"])):
        if wanted is not None and record["owner"] not in wanted:
            continue
        target = Path(record["target"])
        op = record["op"]
        try:
            if op == "tree":
                current = _tree_state(target)
                present = current is not None
                value_hash = vhash(current) if present else None
            elif op == "file":
                data = _current(target)
                present, value_hash = (
                    data is not None,
                    vhash(bhash(data)) if data is not None else None,
                )
            else:
                key = (target, op)
                if key not in documents:
                    documents[key] = Document(target, _format(op)) if target.exists() else None
                document = documents[key]
                present, value = (
                    document.get(op, tuple(record["identity"])) if document else (False, None)
                )
                value_hash = vhash(value) if present else None
            state = (
                "ok"
                if value_hash == record["written_hash"]
                else ("missing" if not present else "drifted")
            )
        except (EngineError, OSError, UnicodeDecodeError) as error:
            state = f"unreadable ({error})"
        rows.append(
            {
                "owner": record["owner"],
                "item": record["item"],
                "harness": record["harness"],
                "target": str(target),
                "identity": record["identity"],
                "state": state,
                "preexisting": bool(record.get("preexisting")),
            }
        )
    return rows


# -- eject ----------------------------------------------------------------------------------


def eject_plans(paths: Paths, marketplace_copy: Path) -> list[Plan]:
    """Plans that leave kitctl's insertions as plain native config and drop all ownership.

    Markers around AGENTS.md blocks are removed (the text stays), a marketplace served
    from the warehouse is repointed at an independent copy, and kit.lock files go.
    Capture hooks are not handled here: purge the ``capture`` owner first.
    """
    ledger = Ledger(paths)
    plans = []
    for owner in ledger.owners():
        result = Plan(owner)
        documents: dict[Path, Document] = {}
        for record in ledger.owned(owner).values():
            target = Path(record["target"])
            identity = tuple(record["identity"])
            effect = _effect_from(record)
            if record["op"] == "block":
                document = documents.setdefault(target, Document(target, "text"))
                found = document._block(identity[0])
                if found:
                    text: str = document.data
                    document.data = text[: found[0]] + found[2] + text[found[1] :]
            elif identity == ("extraKnownMarketplaces", "kitctl"):
                document = documents.setdefault(target, Document(target, "json"))
                present, value = document.get(record["op"], identity)
                if present and isinstance(value, dict):
                    moved = json.loads(json.dumps(value))
                    moved["source"]["path"] = str(marketplace_copy)
                    document.set(record["op"], identity, moved)
            result.changes.append(
                Change("release", effect.item, effect.harness, effect.op, target, identity)
            )
        for document in documents.values():
            after = document.render()
            if after != document.before:
                mode = document.path.stat().st_mode & 0o7777 if document.before else None
                result.writes.append(FileWrite(document.path, document.before, after, mode))
        if owner != "user" and not owner.startswith("capture"):
            lock = Path(owner) / LOCK_FILE
            if lock.is_file():
                result.writes.append(FileWrite(lock, lock.read_bytes(), None))
        plans.append(result)
    return plans


def marketplace_writes(source: Path, target: Path) -> list[FileWrite]:
    writes = []
    for rel, path in source_files(source).items():
        destination = target / rel
        before = destination.read_bytes() if destination.is_file() else None
        data = path.read_bytes()
        if before != data:
            writes.append(FileWrite(destination, before, data, path.stat().st_mode & 0o777))
    return writes
