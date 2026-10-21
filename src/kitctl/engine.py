"""Plan and apply loadouts into one project, with an ownership ledger and a journal.

Layout inside the project:
  kit.yaml   intent: which loadouts (and profiles) are active, for which harnesses
  kit.lock   ledger: every owned insertion, its owners, created parents and dirs
  .kit/      local recovery state (self-gitignored): lock, journal, preimages

Planning only reads. Applying verifies the planned preimages again under a lock,
stages every changed file beside its target, records a journal, then replaces
files one by one. A crash between replacements is recovered from the journal on
the next run. Drift and unmanaged values are conflicts, never takeovers.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

from kitctl import __version__, formats
from kitctl.formats import MISSING, FormatError, Style, detect_style
from kitctl.model import DEFAULT_HARNESSES, HARNESSES, ModelError, is_traversal
from kitctl.resolve import resolve, split_target

INTENT = "kit.yaml"
LEDGER = "kit.lock"
STATE = ".kit"
JOURNAL = f"{STATE}/journal.json"
LOCKFILE = f"{STATE}/lock"
PREIMAGES = f"{STATE}/preimages"
LEDGER_VERSION = 1
# User scope (the root is home): intent and ledger live in .kit/, never loose in ~.
USER_INTENT = f"{STATE}/kit.yaml"
USER_LEDGER = f"{STATE}/kit.lock"


def is_user_scope(repo: Path) -> bool:
    """Kit runs in user scope when the root is the home directory (`--user`)."""
    return repo.resolve() == Path.home().resolve()


def intent_path(repo: Path) -> str:
    return USER_INTENT if is_user_scope(repo) else INTENT


def ledger_path(repo: Path) -> str:
    return USER_LEDGER if is_user_scope(repo) else LEDGER


SANDBOX_ENV = "KITCTL_SANDBOX"
JSON_TARGETS = {
    ".claude/settings.json",
    ".mcp.json",
    ".codex/hooks.json",
    ".cursor/mcp.json",
    ".cursor/hooks.json",
}


class EngineError(Exception):
    """Refuse to continue; nothing has been written."""


class ConflictError(EngineError):
    def __init__(self, conflicts: list[str]) -> None:
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def warehouse_commit(warehouse: Path) -> str | None:
    if not (warehouse / ".git").exists():
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(warehouse), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


def _git(repo: Path, *args: str, strip: bool = True) -> str | None:
    """Stdout of a git command in repo (stripped by default), or None when git fails."""
    # GIT_DIR and friends (set inside git hooks and `rebase -x`) would point the checks at
    # another repository, so git sees only this one.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() if strip else done.stdout


def write_warnings(repo: Path) -> list[str]:
    """What to know before Kit writes here. None of it blocks: `kitctl off` restores the
    original bytes from Kit's own preimages in .kit/, with or without git."""
    if is_user_scope(repo):
        return ["user scope: every session on this machine sees these changes"]
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        return ["not a git repository: `kitctl off` is the undo (Kit keeps the original bytes)"]
    warnings = []
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        warnings.append("tracked files have uncommitted changes; Kit's edits will mix with them")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote_head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    protected = {"main", "master"}
    if remote_head and "/" in remote_head:
        protected.add(remote_head.split("/", 1)[1])
    if branch in protected:
        warnings.append(f"on {branch}; a branch lets the change be reviewed before it lands")
    return warnings


def check_write_allowed(repo: Path) -> None:
    """When $KITCTL_SANDBOX is set, every write stays inside it (tests, CI, a first try).

    Kit's own state is checked too: a symlinked .kit/ or .kit/preimages would carry the
    preimages, journal and lock somewhere else, sandbox or not.
    """
    for rel in (STATE, PREIMAGES):
        if (repo / rel).is_symlink():
            raise EngineError(f"{rel} is a symlink; Kit keeps its state in the project only")
    sandbox = os.environ.get(SANDBOX_ENV)
    if not sandbox:
        return
    try:
        repo.resolve().relative_to(Path(sandbox).resolve())
    except ValueError:
        raise EngineError(
            f"{repo} is outside {SANDBOX_ENV}={sandbox}; refusing to write "
            f"(unset {SANDBOX_ENV} to write here)"
        ) from None


def _yaml() -> YAML:
    yaml = YAML()  # round-trip: keeps comments in kit.yaml
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def read_intent(repo: Path) -> dict[str, Any]:
    path = repo / intent_path(repo)
    if not path.exists():
        return {"schema_version": 1, "loadouts": [], "harnesses": list(DEFAULT_HARNESSES)}
    data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or {}
    # `live` was the earlier opt-in to write outside the sandbox; still read, no longer needed.
    keys = {"schema_version", "loadouts", "harnesses", "live"}
    if not isinstance(data, dict) or set(data) - keys:
        raise EngineError(f"{path}: expected schema_version, loadouts, harnesses and live only")
    if "live" in data and not isinstance(data["live"], bool):
        raise EngineError(f"{path}: live must be true or false")
    if data.get("schema_version") != 1:
        raise EngineError(f"{path}: unsupported schema_version {data.get('schema_version')!r}")
    loadouts = data.get("loadouts") or []
    harnesses = data.get("harnesses") or list(DEFAULT_HARNESSES)
    if not all(isinstance(item, str) for item in loadouts):
        raise EngineError(f"{path}: loadouts must be strings like brand-studio[:profile]")
    names = [split_target(item)[0] for item in loadouts]
    if len(set(names)) != len(names):
        raise EngineError(f"{path}: a loadout may appear once (one profile at a time)")
    if not isinstance(harnesses, list) or not set(harnesses) <= set(HARNESSES):
        raise EngineError(f"{path}: harnesses must be a list of {', '.join(HARNESSES)}")
    return {"schema_version": 1, "loadouts": list(loadouts), "harnesses": list(harnesses)}


def render_intent(repo: Path, intent: dict[str, Any]) -> bytes | None:
    """kit.yaml bytes for an intent, keeping comments of an existing file."""
    path = repo / intent_path(repo)
    if not intent["loadouts"] and not path.exists():
        return None
    yaml = _yaml()
    crlf = False
    if path.exists():
        text = path.read_bytes().decode("utf-8")
        crlf = "\r\n" in text
        data = yaml.load(text)
        data["loadouts"] = intent["loadouts"]
        data["harnesses"] = intent["harnesses"]
    else:
        data = {
            "schema_version": 1,
            "loadouts": intent["loadouts"],
            "harnesses": intent["harnesses"],
        }
    from io import StringIO

    stream = StringIO()
    yaml.dump(data, stream)
    out = stream.getvalue().replace("\r\n", "\n")
    return (out.replace("\n", "\r\n") if crlf else out).encode("utf-8")


# Ledger ------------------------------------------------------------------------------------------


def entry_id(entry: dict[str, Any]) -> str:
    base = f"{entry['target']}#{entry['key']}"
    return f"{base}={_canonical(entry['value'])}" if entry["key"].endswith("/-") else base


def _checksum(body: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def empty_ledger() -> dict[str, Any]:
    return {"entries": [], "created_dirs": [], "created_files": [], "loadouts": []}


def read_ledger(repo: Path) -> dict[str, Any]:
    path = repo / ledger_path(repo)
    if not path.exists():
        return empty_ledger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        body = data["body"]
        # Explicit checks, not assert: these must also run under `python -O`.
        if data["schema_version"] != LEDGER_VERSION:
            raise ValueError("unsupported schema_version")
        if data["checksum"] != _checksum(body):
            raise ValueError("checksum mismatch")
        if not all(
            isinstance(e, dict) and {"target", "key", "value", "owners"} <= set(e)
            for e in body["entries"]
        ):
            raise ValueError("malformed entries")
    except (ValueError, KeyError, TypeError) as error:
        raise EngineError(
            f"{path} is corrupted or was edited by hand ({type(error).__name__}); Kit will not "
            "guess which entries it owns. Restore it from version control, or remove the "
            "loadout's entries by hand and delete kit.lock."
        ) from error
    return dict(body)


def render_ledger(body: dict[str, Any], commit: str | None) -> bytes:
    body = {**body, "warehouse_commit": commit, "kitctl": __version__}
    document = {"schema_version": LEDGER_VERSION, "checksum": _checksum(body), "body": body}
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# Desired state -----------------------------------------------------------------------------------


@dataclass
class Desired:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    setup: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _expand_directories(
    warehouse: Path, effects: list[dict[str, Any]], conflicts: list[str]
) -> list[dict[str, Any]]:
    """A skill directory becomes one whole file per asset, each owned by path like a generated
    rule file, so `off` removes exactly what Kit copied and drift shows per file."""
    out = []
    for effect in effects:
        if effect["format"] != "directory":
            out.append(effect)
            continue
        kind, _, name = effect["source"].partition(":")
        root = warehouse / "primitives" / f"{kind}s" / PurePosixPath(name)
        if not root.is_dir() or not (root / "SKILL.md").is_file():
            conflicts.append(
                f"{effect['source']}: no skill directory with a SKILL.md in the warehouse"
            )
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                conflicts.append(f"{effect['source']}: {rel} is a symlink; Kit copies files only")
                continue
            try:
                # Owned whole files are LF, like the content hash; a CRLF checkout of the
                # warehouse must not read as drift on every apply.
                text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
            except UnicodeDecodeError:
                conflicts.append(f"{effect['source']}: {rel} is not UTF-8 text; copy it by hand")
                continue
            out.append(
                {
                    **effect,
                    "target": f"{effect['target']}{rel}",
                    "format": "file",
                    "key": effect["source"],
                    "value": text,
                }
            )
    return out


def _user_targets() -> tuple[dict[str, str], dict[str, str]]:
    """Prefix map for user scope: .claude/ and .codex/ move with CLAUDE_CONFIG_DIR and
    CODEX_HOME, but only to a folder under home other than Kit's own .kit/. A bad value is
    a problem only for the effects that would use it (prefix -> problem)."""
    home = Path.home().resolve()
    prefixes: dict[str, str] = {}
    problems: dict[str, str] = {}
    for prefix, env in ((".claude/", "CLAUDE_CONFIG_DIR"), (".codex/", "CODEX_HOME")):
        value = os.environ.get(env)
        if not value:
            continue
        try:
            rel = Path(value).expanduser().resolve().relative_to(home).as_posix()
        except ValueError:
            problems[prefix] = f"{env}={value} is outside home; user scope writes only under home"
            continue
        if rel == "." or rel == STATE or rel.startswith(f"{STATE}/"):
            problems[prefix] = f"{env}={value} must name a folder under home, not home or {STATE}/"
            continue
        prefixes[prefix] = f"{rel}/"
    return prefixes, problems


def desired_state(warehouse: Path, intent: dict[str, Any], user: bool = False) -> Desired:
    result = Desired()
    prefixes: dict[str, str] = {}
    problems: dict[str, str] = {}
    if user:
        prefixes, problems = _user_targets()
    for target in intent["loadouts"]:
        name, profile = split_target(target)
        owner = {"loadout": name, "profile": profile}
        for harness in intent["harnesses"]:
            try:
                plan = resolve(warehouse, target, harness, scope="user" if user else "project")
            except ModelError as error:
                raise EngineError(f"{target} ({harness}): {error}") from error
            effects = list(plan["effects"])
            block = plan["instructions"]
            if block:
                effects.append(
                    {
                        "target": block["target"],
                        "format": block["format"],
                        "key": block["marker"],
                        "value": block["text"],
                    }
                )
            for effect in _expand_directories(warehouse, effects, result.conflicts):
                bad = [p for prefix, p in problems.items() if effect["target"].startswith(prefix)]
                if bad:
                    result.conflicts.extend(c for c in bad if c not in result.conflicts)
                    continue
                for old, new in prefixes.items():
                    if effect["target"].startswith(old):
                        effect = {**effect, "target": new + effect["target"][len(old) :]}
                if "${plugin:" in _canonical(effect["value"]):
                    result.conflicts.append(
                        f"{effect.get('source', effect['target'])} ({harness}): needs the "
                        "installed plugin's path, which Kit cannot know before install; use the "
                        "plugin's own hook (or its installer) instead"
                    )
                    continue
                entry = {
                    "harness": harness,
                    "target": effect["target"],
                    "format": effect["format"],
                    "key": effect["key"],
                    "value": effect["value"],
                    "owners": [owner],
                }
                ident = entry_id(entry)
                existing = result.entries.get(ident)
                if existing is None:
                    result.entries[ident] = entry
                elif _canonical(existing["value"]) != _canonical(entry["value"]):
                    result.conflicts.append(
                        f"{ident}: {target} and {existing['owners'][0]['loadout']} want "
                        "different values"
                    )
                elif owner not in existing["owners"]:
                    existing["owners"].append(owner)
            for gap in plan["gaps"]:
                result.gaps.append({**gap, "loadout": target})
            for step in plan["setup"]:
                if step not in result.setup:
                    result.setup.append(step)
    return result


# Setup -------------------------------------------------------------------------------------------

PLUGIN_REF = re.compile(r"\$\{plugin:([A-Za-z0-9._-]+)\}")


def _installed_plugin_paths(home: Path) -> dict[str, str]:
    """Plugin name -> install path, from Claude Code's installed_plugins.json."""
    from kitctl.native import read_native

    paths: dict[str, str] = {}
    for item in read_native(home.expanduser().resolve(), home)["installed_plugins"]:
        name = str(item.get("id", "")).split("@", 1)[0]
        if item.get("path") and name not in paths:
            paths[name] = str(item["path"])
    return paths


def setup_runs(
    repo: Path, steps: list[dict[str, Any]], home: Path | None = None
) -> list[dict[str, Any]]:
    """Each setup step ready to run (`argv`), or why it is skipped (`skip`). Reads only.

    A step's `when: file-exists:PATH` is checked in the project, and `${plugin:NAME}` becomes
    the installed plugin's directory. Setup outputs are the tool's, never Kit-owned.
    """
    installed: dict[str, str] | None = None
    runs: list[dict[str, Any]] = []
    for step in steps:
        when = str(step.get("when") or "")
        if when.startswith("file-exists:") and not (repo / PurePosixPath(when[12:])).exists():
            runs.append({"step": step, "argv": None, "skip": f"{when[12:]} not found"})
            continue
        argv: list[str] = []
        skip: str | None = None
        for part in step["run"]:
            for name in PLUGIN_REF.findall(str(part)):
                if installed is None:
                    installed = _installed_plugin_paths(home or Path.home())
                if name not in installed:
                    skip = f"plugin {name} is not installed (Claude Code); install it, then re-run"
                    break
                part = str(part).replace(f"${{plugin:{name}}}", installed[name])
            argv.append(str(part))
        runs.append({"step": step, "argv": None if skip else argv, "skip": skip})
    return runs


# Files -------------------------------------------------------------------------------------------


class Doc:
    """One native file being edited in memory.

    Formats: "json" and "toml" (owned keys), "markdown-block" (a marked region Kit appends)
    and "file" (a whole generated file Kit owns by path; its one entry's value is the text).
    """

    def __init__(self, repo: Path, target: str, fmt: str) -> None:
        if is_traversal(target):
            raise EngineError(f"refusing target outside the project: {target}")
        self.target = target
        self.fmt = fmt
        self.path = repo / PurePosixPath(target)
        self.conflict: str | None = None
        self.cleared = False
        if self.path.is_symlink() or any(
            p.is_symlink() for p in self.path.parents if p.is_relative_to(repo) and p != repo
        ):
            self.conflict = f"{target} is a symlink; Kit will not write through links"
        self.before = self.path.read_bytes() if self.path.is_file() else None
        try:
            text = self.before.decode("utf-8") if self.before else ""
        except UnicodeDecodeError:
            text, self.conflict = "", f"{target} is not UTF-8 text"
        self.style: Style = detect_style(text)
        self.text = text
        self.doc: Any = None
        try:
            if fmt == "json":
                self.doc = formats.json_load(text)
                if not isinstance(self.doc, dict):
                    raise FormatError("top level is not an object")
            elif fmt == "toml":
                self.doc = formats.toml_load(text)
        except FormatError as error:
            self.conflict = f"{target}: {error}"
        self.changed = False

    def get(self, key: str, value: Any) -> Any:
        try:
            return self._get(key, value)
        except FormatError as error:
            self.conflict = f"{self.target}: {error}"
            return MISSING

    def _get(self, key: str, value: Any) -> Any:
        if self.fmt == "json":
            current = formats.json_get(self.doc, key)
            if key.endswith("/-"):
                return value if current is not MISSING and value in current else MISSING
            return current
        if self.fmt == "toml":
            return formats.toml_get(self.doc, key, value)
        if self.fmt == "file":
            # A whole file Kit just took back reads as absent, so an update re-inserts it.
            if self.before is None or self.cleared:
                return MISSING
            return self.text.replace("\r\n", "\n")
        return formats.block_get(self.text, key)

    def insert(self, key: str, value: Any) -> tuple[list[str], dict[str, Any]]:
        """Insert; return (containers created, extra ledger fields)."""
        self.changed = True
        if self.fmt == "json":
            return formats.json_set(self.doc, key, value), {}
        if self.fmt == "toml":
            return formats.toml_set(self.doc, key, value), {}
        if self.fmt == "file":
            self.text = value  # only when absent or just taken back: Kit never overwrites one
            self.cleared = False
            return [], {}
        self.text, separator = formats.block_insert(self.text, key, value)
        return [], {"separator": separator}

    def exists(self, parent: str) -> bool:
        if self.fmt == "json":
            return formats.json_get(self.doc, parent) is not MISSING
        return formats.toml_get(self.doc, parent) is not MISSING

    def remove(self, entry: dict[str, Any], created: list[str]) -> None:
        self.changed = True
        if self.fmt == "json":
            formats.json_delete(self.doc, entry["key"], entry["value"], created)
        elif self.fmt == "toml":
            formats.toml_delete(self.doc, entry["key"], created, entry["value"])
        elif self.fmt == "file":
            self.text = ""  # empty: deleted below because Kit created it
            self.cleared = True
        else:
            self.text = formats.block_remove(self.text, entry["key"], entry.get("separator", ""))

    def empty(self) -> bool:
        if self.fmt == "json":
            return bool(self.doc == {})
        if self.fmt == "toml":
            return not self.doc.unwrap() and not formats.toml_dump(self.doc, self.style).strip()
        return not self.text.strip()

    def parsed(self, data: bytes) -> Any:
        text = data.decode("utf-8")
        if self.fmt == "json":
            return formats.json_load(text)
        if self.fmt == "toml":
            return formats.toml_load(text).unwrap()
        return text.replace("\r\n", "\n")

    def render(self) -> bytes:
        if self.fmt == "json":
            return formats.json_dump(self.doc, self.style).encode("utf-8")
        if self.fmt == "toml":
            return formats.toml_dump(self.doc, self.style).encode("utf-8")
        return self.text.encode("utf-8")


# Plan --------------------------------------------------------------------------------------------


@dataclass
class Plan:
    repo: Path
    intent: dict[str, Any]
    ops: list[dict[str, Any]]
    files: dict[str, tuple[bytes | None, bytes | None]]
    ledger: dict[str, Any]
    preimages: dict[str, bytes]
    remove_dirs: list[str]
    gaps: list[dict[str, Any]]
    setup: list[dict[str, Any]]
    conflicts: list[str]

    @property
    def changes(self) -> dict[str, tuple[bytes | None, bytes | None]]:
        return {t: (b, a) for t, (b, a) in self.files.items() if b != a}

    def diff(self) -> str:
        chunks: list[str] = []
        for target, (before, after) in sorted(self.changes.items()):
            old = (before or b"").decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
            new = (after or b"").decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
            chunks.extend(
                difflib.unified_diff(
                    old,
                    new,
                    f"a/{target}" if before is not None else "/dev/null",
                    f"b/{target}" if after is not None else "/dev/null",
                )
            )
        return "".join(line if line.endswith("\n") else line + "\n" for line in chunks)

    def summary(self) -> dict[str, Any]:
        return {
            "repo": str(self.repo),
            "loadouts": self.intent["loadouts"],
            "operations": self.ops,
            "files": sorted(self.changes),
            "gaps": self.gaps,
            "setup": self.setup,
            "conflicts": self.conflicts,
        }


def _preimage_path(repo: Path, target: str) -> Path:
    return repo / PREIMAGES / hashlib.sha256(target.encode()).hexdigest()


def make_plan(repo: Path, warehouse: Path, intent: dict[str, Any]) -> Plan:
    """Compute every file change for an intent. Reads only."""
    if (repo / JOURNAL).exists():
        raise EngineError("an earlier apply was interrupted; run `kitctl apply` to recover first")
    ledger = read_ledger(repo)
    desired = desired_state(warehouse, intent, is_user_scope(repo))
    conflicts = list(desired.conflicts)
    owned = {entry_id(e): e for e in ledger["entries"]}
    docs: dict[str, Doc] = {}

    def doc(target: str, fmt: str) -> Doc:
        if target not in docs:
            docs[target] = Doc(repo, target, fmt)
            problem = docs[target].conflict
            if problem:
                conflicts.append(problem)
        return docs[target]

    ops: list[dict[str, Any]] = []
    new_entries: list[dict[str, Any]] = []
    # Containers Kit created, per file; any owned removal may take back an empty one.
    created_parents: dict[str, list[str]] = {
        t: list(v) for t, v in ledger.get("created_parents", {}).items()
    }
    # Removals and drift checks first, so additions see the final shape.
    for ident, entry in sorted(owned.items()):
        current_doc = doc(entry["target"], entry["format"])
        if current_doc.conflict:
            continue
        current = current_doc.get(entry["key"], entry["value"])
        if current_doc.conflict:
            conflicts.append(current_doc.conflict)
            continue
        wanted = desired.entries.get(ident)
        drifted = current is MISSING or _canonical(current) != _canonical(entry["value"])
        if wanted is not None and _canonical(wanted["value"]) == _canonical(entry["value"]):
            if drifted:
                conflicts.append(
                    f"drift: {ident} was changed outside Kit; resolve it by hand, "
                    "then run apply again"
                )
            new_entries.append({**entry, "owners": wanted["owners"]})
            if wanted["owners"] != entry["owners"]:
                ops.append({"action": "reattribute", "id": ident, "owners": wanted["owners"]})
            continue
        if entry.get("preexisting"):
            ops.append({"action": "release", "id": ident})  # Kit never wrote it; leave it.
            continue
        if drifted:
            conflicts.append(f"drift: {ident} was changed outside Kit; Kit will not remove it")
            new_entries.append(entry)
            continue
        try:
            current_doc.remove(entry, created_parents.get(entry["target"], []))
        except FormatError as error:
            conflicts.append(f"{ident}: {error}")
            new_entries.append(entry)
            continue
        ops.append({"action": "remove", "id": ident})
    kept = {entry_id(e) for e in new_entries}
    for ident, wanted in sorted(desired.entries.items()):
        if ident in kept:
            continue
        current_doc = doc(wanted["target"], wanted["format"])
        if current_doc.conflict:
            continue
        current = current_doc.get(wanted["key"], wanted["value"])
        if current_doc.conflict:
            conflicts.append(current_doc.conflict)
            continue
        if current is not MISSING and _canonical(current) == _canonical(wanted["value"]):
            new_entries.append({**wanted, "preexisting": True})
            ops.append({"action": "adopt", "id": ident})
            continue
        if current is not MISSING and not wanted["key"].endswith("/-"):
            conflicts.append(
                f"unmanaged: {ident} already has a different value; Kit will not take it over"
            )
            continue
        try:
            created, details = current_doc.insert(wanted["key"], wanted["value"])
        except FormatError as error:
            conflicts.append(f"{ident}: {error}")
            continue
        created_parents.setdefault(wanted["target"], []).extend(created)
        new_entries.append({**wanted, **details, "preexisting": False})
        ops.append({"action": "add", "id": ident, "owners": wanted["owners"]})

    files: dict[str, tuple[bytes | None, bytes | None]] = {}
    preimages: dict[str, bytes] = {}
    created_files = set(ledger.get("created_files", []))
    still_owned = {e["target"] for e in new_entries if not e.get("preexisting")}
    for target, current_doc in sorted(docs.items()):
        if current_doc.conflict or not current_doc.changed:
            files[target] = (current_doc.before, current_doc.before)
            continue
        after: bytes | None = current_doc.render()
        stored = _preimage_path(repo, target)
        if current_doc.before is not None and target not in created_files and not stored.exists():
            preimages[target] = current_doc.before
        if target not in still_owned:
            if target in created_files and current_doc.empty():
                after = None
            elif stored.exists():
                baseline = stored.read_bytes()
                with contextlib.suppress(FormatError, UnicodeDecodeError):
                    if (
                        after is not None
                        and current_doc.parsed(after) == current_doc.parsed(baseline)
                        and _comments(current_doc.fmt, after)
                        == _comments(current_doc.fmt, baseline)
                    ):
                        after = baseline  # Byte-for-byte baseline when nothing else changed.
        if current_doc.before is None and after is not None:
            created_files.add(target)
        files[target] = (current_doc.before, after)
    created_files = {t for t in created_files if t in still_owned}
    created_parents = {
        target: sorted({p for p in parents if docs[target].exists(p)})
        if target in docs
        else sorted(set(parents))
        for target, parents in created_parents.items()
        if target in still_owned
    }
    created_parents = {t: v for t, v in created_parents.items() if v}

    created_dirs = set(ledger.get("created_dirs", []))
    for target in still_owned:
        for parent in PurePosixPath(target).parents:
            if str(parent) != "." and not (repo / parent).exists():
                created_dirs.add(str(parent))
    remove_dirs = sorted(
        (d for d in created_dirs if not any(t.startswith(d + "/") for t in still_owned)),
        key=lambda d: -d.count("/"),
    )
    created_dirs -= set(remove_dirs)

    commit = warehouse_commit(warehouse)
    body = {
        "loadouts": [
            {"loadout": split_target(t)[0], "profile": split_target(t)[1]}
            for t in intent["loadouts"]
        ],
        "harnesses": intent["harnesses"],
        "entries": sorted(new_entries, key=entry_id),
        "created_files": sorted(created_files),
        "created_parents": created_parents,
        "created_dirs": sorted(created_dirs),
        "kit_yaml_created": ledger.get("kit_yaml_created", not (repo / intent_path(repo)).exists()),
    }
    ledger_rel, intent_rel = ledger_path(repo), intent_path(repo)
    lock_before = (repo / ledger_rel).read_bytes() if (repo / ledger_rel).exists() else None
    intent_before = (repo / intent_rel).read_bytes() if (repo / intent_rel).exists() else None
    if intent["loadouts"]:
        files[ledger_rel] = (lock_before, render_ledger(body, commit))
        files[intent_rel] = (intent_before, render_intent(repo, intent))
    else:
        files[ledger_rel] = (lock_before, None)
        files[intent_rel] = (
            intent_before,
            None if body["kit_yaml_created"] else render_intent(repo, intent),
        )
    return Plan(
        repo,
        intent,
        ops,
        files,
        body,
        preimages,
        remove_dirs,
        desired.gaps,
        desired.setup,
        sorted(set(conflicts)),
    )


# Apply -------------------------------------------------------------------------------------------


def _comments(fmt: str, data: bytes) -> list[str]:
    """TOML comments are not part of the parsed value; compare them separately."""
    if fmt != "toml":
        return []
    return re.findall(r"#[^\r\n]*", data.decode("utf-8", errors="replace"))


def _linked(repo: Path, target: str) -> bool:
    path = repo / PurePosixPath(target)
    return path.is_symlink() or any(
        p.is_symlink() for p in path.parents if p.is_relative_to(repo) and p != repo
    )


def _create_new(path: Path, data: bytes) -> None:
    """Write a fresh file without following a link planted at its name."""
    if path.is_symlink():
        raise ConflictError([f"{path.name} is a symlink; Kit will not write through it"])
    with contextlib.suppress(FileNotFoundError):
        path.unlink()  # a leftover from an earlier crash
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    with os.fdopen(os.open(path, flags, 0o644), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextlib.contextmanager
def project_lock(repo: Path) -> Any:
    state = repo / STATE
    if state.is_symlink():
        raise EngineError(f"{state} is a symlink; Kit keeps its state only in a real directory")
    state.mkdir(exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    try:
        handle = os.open(repo / LOCKFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise EngineError(
            f"another kitctl run holds {repo / LOCKFILE}; if none is running, delete it"
        ) from error
    try:
        os.write(handle, str(os.getpid()).encode())
        os.close(handle)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            (repo / LOCKFILE).unlink()


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".kit-tmp")
    _create_new(tmp, data)
    os.replace(tmp, path)


def _commit_files(repo: Path, journal: dict[str, Any]) -> None:
    for item in journal["files"]:
        path = repo / PurePosixPath(item["target"])
        current = path.read_bytes() if path.is_file() else None
        if _sha(current) == item["after"]:
            continue
        if _sha(current) != item["before"]:
            raise ConflictError(
                [
                    f"{item['target']} changed during apply; journal kept at "
                    f"{JOURNAL} for manual recovery"
                ]
            )
        if item["after"] is None:
            path.unlink()
        else:
            staged = repo / PurePosixPath(item["staged"])
            os.replace(staged, path)


def _clear_state(repo: Path) -> None:
    """Drop Kit's state. A project's .kit/ is Kit's alone; ~/.kit may hold other tools'
    files, so in user scope only Kit's own files go, and the folder only if it is empty."""
    state = repo / STATE
    if not is_user_scope(repo):
        shutil.rmtree(state, ignore_errors=True)
        return
    for rel in (JOURNAL, LOCKFILE, USER_INTENT, USER_LEDGER):
        with contextlib.suppress(FileNotFoundError):
            (repo / rel).unlink()
    shutil.rmtree(repo / PREIMAGES, ignore_errors=True)
    ignore = state / ".gitignore"
    with contextlib.suppress(OSError):
        if ignore.read_text(encoding="utf-8") == "*\n":
            ignore.unlink()
    with contextlib.suppress(OSError):
        state.rmdir()


def _finish(repo: Path, journal: dict[str, Any]) -> None:
    for directory in journal["remove_dirs"]:
        with contextlib.suppress(OSError):
            (repo / directory).rmdir()  # only if empty: user content keeps it
    for target in journal["drop_preimages"]:
        with contextlib.suppress(FileNotFoundError):
            _preimage_path(repo, target).unlink()
    (repo / JOURNAL).unlink()
    if journal["clear_state"]:
        _clear_state(repo)


def recover(repo: Path) -> bool:
    """Roll an interrupted apply forward. Returns True if there was something to recover."""
    path = repo / JOURNAL
    if not path.exists():
        return False
    check_write_allowed(repo)
    journal = json.loads(path.read_text(encoding="utf-8"))
    with project_lock(repo):
        _commit_files(repo, journal)
        _finish(repo, journal)
    return True


def apply_plan(plan: Plan) -> list[str]:
    """Write a plan. Returns the targets written. Raises without writing on conflicts."""
    repo = plan.repo
    if not plan.changes and not plan.remove_dirs and not plan.conflicts:
        return []  # nothing to write needs no permission (as after a recovery)
    check_write_allowed(repo)
    if plan.conflicts:
        raise ConflictError(plan.conflicts)
    changes = plan.changes
    if not changes and not plan.remove_dirs:
        return []
    with project_lock(repo):
        # Preimages again, under the lock: anything that moved since planning is a conflict.
        moved = [
            t
            for t, (before, _) in changes.items()
            if _sha(
                (repo / PurePosixPath(t)).read_bytes()
                if (repo / PurePosixPath(t)).is_file()
                else None
            )
            != _sha(before)
        ]
        if moved:
            raise ConflictError(
                [f"{t} changed after the plan was made; plan again" for t in sorted(moved)]
            )
        linked = [t for t in changes if _linked(repo, t)]
        if linked:
            raise ConflictError(
                [f"{t} is now behind a symlink; plan again" for t in sorted(linked)]
            )
        for target, data in plan.preimages.items():
            stored = _preimage_path(repo, target)
            stored.parent.mkdir(parents=True, exist_ok=True)
            if not stored.exists():
                _write_atomic(stored, data)
        files = []
        for index, (target, (before, after)) in enumerate(sorted(changes.items())):
            path = repo / PurePosixPath(target)
            staged = None
            if after is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                staged_path = path.with_name(f"{path.name}.kit-stage-{index}")
                _create_new(staged_path, after)
                if path.exists():
                    shutil.copymode(path, staged_path)
                staged = staged_path.relative_to(repo).as_posix()
            files.append(
                {"target": target, "before": _sha(before), "after": _sha(after), "staged": staged}
            )
        still = {e["target"] for e in plan.ledger["entries"] if not e.get("preexisting")}
        journal = {
            "files": files,
            "remove_dirs": plan.remove_dirs,
            "drop_preimages": [t for t in changes if t not in still],
            # In user scope the intent lives in .kit/; keep the folder if Kit did not create it.
            "clear_state": not plan.intent["loadouts"]
            and not (is_user_scope(repo) and not plan.ledger.get("kit_yaml_created", True)),
        }
        _write_atomic(repo / JOURNAL, json.dumps(journal, indent=2).encode())
        _commit_files(repo, journal)
        _finish(repo, journal)
    return sorted(changes)


# Commands ----------------------------------------------------------------------------------------


def with_loadout(intent: dict[str, Any], target: str) -> dict[str, Any]:
    name, _ = split_target(target)
    loadouts = [t for t in intent["loadouts"] if split_target(t)[0] != name]
    position = next(
        (i for i, t in enumerate(intent["loadouts"]) if split_target(t)[0] == name), len(loadouts)
    )
    loadouts.insert(position, target)
    return {**intent, "loadouts": loadouts}


def without_loadout(intent: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in [split_target(t)[0] for t in intent["loadouts"]]:
        raise EngineError(f"{name} is not active in this project")
    return {**intent, "loadouts": [t for t in intent["loadouts"] if split_target(t)[0] != name]}


def status(repo: Path, warehouse: Path) -> dict[str, Any]:
    ledger = read_ledger(repo)
    intent = read_intent(repo)
    entries = []
    for entry in ledger["entries"]:
        current_doc = Doc(repo, entry["target"], entry["format"])
        if current_doc.conflict:
            state = "unreadable"
        else:
            current = current_doc.get(entry["key"], entry["value"])
            state = (
                "missing"
                if current is MISSING
                else "ok"
                if _canonical(current) == _canonical(entry["value"])
                else "drift"
            )
        entries.append(
            {
                "id": entry_id(entry),
                "owners": entry["owners"],
                "state": state,
                "preexisting": entry.get("preexisting", False),
            }
        )
    report: dict[str, Any] = {
        "repo": str(repo),
        "loadouts": intent["loadouts"],
        "interrupted": (repo / JOURNAL).exists(),
        "entries": entries,
        "slots": {},
        "pending_trust": [],
    }
    for target in intent["loadouts"]:
        for harness in intent["harnesses"]:
            try:
                plan = resolve(
                    warehouse, target, harness, scope="user" if is_user_scope(repo) else "project"
                )
            except ModelError as error:
                report.setdefault("errors", []).append(f"{target} ({harness}): {error}")
                continue
            for slot, info in plan["slots"].items():
                report["slots"].setdefault(harness, {})[slot] = info["provider"]
            report["pending_trust"] += [
                f"{harness} {gap['subject']}"
                for gap in plan["gaps"]
                if gap["severity"] == "trust-required"
            ]
    return report


def eject(repo: Path) -> list[str]:
    """Keep native config as plain files; drop Kit's markers, ledger, intent and state."""
    check_write_allowed(repo)
    if (repo / JOURNAL).exists():
        raise EngineError("an earlier apply was interrupted; run `kitctl apply` to recover first")
    ledger = read_ledger(repo)
    dependent = [
        entry_id(e)
        for e in ledger["entries"]
        if "${plugin:" in _canonical(e["value"]) or "kitctl" in _canonical(e["value"])
    ]
    if dependent:
        raise EngineError(
            "these entries still depend on Kit and cannot be ejected as is: " + ", ".join(dependent)
        )
    touched = []
    with project_lock(repo):
        for target in sorted(
            {e["target"] for e in ledger["entries"] if e["format"] == "markdown-block"}
        ):
            path = repo / PurePosixPath(target)
            text = path.read_text(encoding="utf-8")
            for entry in ledger["entries"]:
                if entry["target"] == target and entry["format"] == "markdown-block":
                    name = entry["key"].split(":", 1)[1]
                    text = formats.strip_markers(text, name)
            _write_atomic(path, text.encode("utf-8"))
            touched.append(target)
        for name in (ledger_path(repo), intent_path(repo)):
            if (repo / name).exists():
                (repo / name).unlink()
                touched.append(name)
    _clear_state(repo)
    return touched
