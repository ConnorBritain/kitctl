"""Warehouse documents: parsing, validation, identities, and content hashes.

Pure reads. Every document carries schema_version and rejects unknown fields.
Version 2 adds the loadout and primitive fields the brand-studio stack needs;
version 1 loadouts (extends, skills, mcp, hooks, plugins) are read and upgraded
in memory. Credentials are named environment references, never literal values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

HARNESSES = ("claude", "codex", "cursor")
# What a project or brief gets when it names no harnesses; adding a harness must not
# widen existing plans.
DEFAULT_HARNESSES = ("claude", "codex")
KINDS = ("skill", "mcp", "hook", "plugin", "marketplace", "tool", "agent")
KIND_DIRS = {
    "skill": "skills",
    "mcp": "mcp",
    "hook": "hooks",
    "plugin": "plugins",
    "marketplace": "marketplaces",
    "tool": "tools",
    "agent": "agents",
}
# An agent's asset directory holds one file per harness, already rendered by its author;
# Kit copies the file and never converts between formats.
AGENT_FILES = {"claude": "claude.md", "codex": "codex.toml"}
CODEX_AGENT_KEYS = ("name", "description", "developer_instructions")
HASH_SCHEME = "kit-hash-v1"
SCHEMA_VERSIONS = (1, 2)
CURRENT_SCHEMA = 2

_SEGMENT = r"[a-z0-9](?:[a-z0-9._-]{0,62})"
NAME_RE = re.compile(rf"^(?:{_SEGMENT}/)?{_SEGMENT}$")
REF_RE = re.compile(rf"^({'|'.join(KINDS)}):((?:{_SEGMENT}/)?{_SEGMENT})$")
SLOT_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$")
SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
WHEN_RE = re.compile(r"^file-exists:(.+)$")
SECRET_RES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"pypi-[A-Za-z0-9_-]{40,}"),
    re.compile(r"npm_[A-Za-z0-9]{36,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"sk-(?:live|proj|ant)-[A-Za-z0-9_-]{24,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
)

COMMON_PRIMITIVE = {
    "schema_version",
    "kind",
    "name",
    "description",
    "tags",
    "harnesses",
    "source",
    "provides",
    "conflicts",
    "hash",
    "external",
}
KIND_FIELDS = {
    "skill": {"plugin"},
    "mcp": {"transport", "command", "url", "pin", "args", "env", "headers"},
    "hook": {
        "event",
        "matcher",
        "command",
        "timeout",
        "blocking",
        "trust",
        "unless_capability",
        "heartbeat",
    },
    "plugin": {"marketplace", "plugin_name", "version", "skills", "hook_provides", "heartbeat"},
    "marketplace": set(),
    "tool": {"check", "install_hint"},
    "agent": set(),
}
V1_PRIMITIVE = {"schema_version", "kind", "name", "description", "tags", "harnesses", "source"}
HARNESS_KEYS = {"id", "skill", "manifest", "path"}
SOURCE_KEYS = {"git", "sha", "tag", "path", "url", "license", "retrieved"}
V1_LOADOUT = {
    "schema_version",
    "name",
    "description",
    "extends",
    "skills",
    "mcp",
    "hooks",
    "plugins",
}
V2_LOADOUT = V1_LOADOUT | {
    "marketplaces",
    "slots",
    "disable",
    "requires",
    "optional",
    "setup",
    "instructions",
    "profiles",
    "agents",
    "budget",
}
LIST_FIELDS = {
    "skills": "skill",
    "agents": "agent",
    "hooks": "hook",
    "plugins": "plugin",
    "marketplaces": "marketplace",
    "requires": "tool",
    "optional": "tool",
}
PROFILE_FIELDS = {
    "description",
    "skills",
    "mcp",
    "hooks",
    "plugins",
    "marketplaces",
    "requires",
    "optional",
    "setup",
    "instructions_append",
    "agents",
    "budget",
}
BUDGET_KEYS = ("tokens", "skills")
SETUP_KEYS = {"run", "when", "outputs", "harness"}
MCP_ENTRY_KEYS = {"primitive", "enabled_tools", "disabled_tools"}


@dataclass(frozen=True, order=True)
class Problem:
    """A validation finding. Codes are stable; messages are for people."""

    path: str
    code: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


class ModelError(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__("; ".join(f"{p.path}: {p.message}" for p in problems))
        self.problems = problems


@dataclass(frozen=True)
class Primitive:
    kind: str
    name: str
    path: str
    data: dict[str, Any]
    content_hash: str
    harnesses: dict[str, dict[str, str]]

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def provides(self) -> list[str]:
        return list(self.data.get("provides", []))

    @property
    def conflicts(self) -> list[str]:
        return list(self.data.get("conflicts", []))

    def supports(self, harness: str) -> bool:
        return harness in self.harnesses


@dataclass
class Profile:
    name: str
    fields: dict[str, Any]


@dataclass
class Loadout:
    name: str
    path: str
    schema_version: int
    description: str = ""
    extends: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    marketplaces: list[str] = field(default_factory=list)
    mcp: dict[str, dict[str, Any]] = field(default_factory=dict)
    slots: dict[str, str] = field(default_factory=dict)
    disable: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    setup: list[dict[str, Any]] = field(default_factory=list)
    instructions: str = ""
    budget: dict[str, int] = field(default_factory=dict)
    profiles: dict[str, Profile] = field(default_factory=dict)


def warehouse_root(explicit: Path | None = None) -> Path:
    """--warehouse, then KIT_WAREHOUSE, then ~/kits."""
    if explicit is not None:
        return explicit.expanduser()
    env = os.environ.get("KIT_WAREHOUSE")
    return Path(env).expanduser() if env else Path.home() / "kits"


def load_yaml(path: Path) -> Any:
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    try:
        with path.open("r", encoding="utf-8") as stream:
            return yaml.load(stream)
    except YAMLError as error:
        first = str(error).strip().splitlines()[0] if str(error).strip() else "invalid YAML"
        raise ModelError([Problem(str(path), "yaml", first)]) from error
    except UnicodeDecodeError as error:
        raise ModelError([Problem(str(path), "yaml", "not UTF-8 text")]) from error


def is_traversal(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (
        normalized.startswith("/")
        or bool(re.match(r"^[A-Za-z]:", value))
        or ".." in normalized.split("/")
        or "\\" in value
    )


def check_relpath(value: Any, where: str, problems: list[Problem]) -> None:
    if not isinstance(value, str) or not value:
        problems.append(Problem(where, "type", "expected a relative path"))
    elif is_traversal(value):
        problems.append(Problem(where, "path-traversal", f"path escapes its root: {value!r}"))


def parse_ref(value: Any, where: str, problems: list[Problem], kind: str | None = None) -> str:
    """Return a qualified kind:name reference, or "" after recording a problem."""
    if not isinstance(value, str):
        problems.append(Problem(where, "type", "expected a reference string"))
        return ""
    candidate = value if ":" in value or kind is None else f"{kind}:{value}"
    if is_traversal(candidate.split(":", 1)[-1]):
        problems.append(
            Problem(where, "path-traversal", f"reference escapes the warehouse: {value!r}")
        )
        return ""
    match = REF_RE.match(candidate)
    if not match:
        problems.append(Problem(where, "reference", f"invalid reference {value!r}"))
        return ""
    if kind is not None and match.group(1) != kind:
        problems.append(Problem(where, "reference", f"expected a {kind} reference, got {value!r}"))
        return ""
    return candidate


def primitive_path(root: Path, ref: str) -> Path:
    kind, name = ref.split(":", 1)
    return root / "primitives" / KIND_DIRS[kind] / f"{name}.yaml"


def loadout_path(root: Path, name: str) -> Path:
    return root / "loadouts" / f"{name}.yaml"


def contained(root: Path, path: Path) -> bool:
    """True when path, after resolving links, stays inside root."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _scan_secrets(value: Any, where: str, problems: list[Problem]) -> None:
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in SECRET_RES):
            problems.append(
                Problem(where, "credential-literal", "looks like a credential (value withheld)")
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _scan_secrets(item, f"{where}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_secrets(item, f"{where}[{index}]", problems)


def _unknown(data: dict[str, Any], allowed: set[str], where: str, problems: list[Problem]) -> None:
    for key in sorted(set(data) - allowed, key=str):
        problems.append(Problem(f"{where}.{key}", "unknown-field", f"unknown field {key!r}"))


def _str_list(value: Any, where: str, problems: list[Problem]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(Problem(where, "type", "expected a list of strings"))
        return []
    return list(value)


def _env_refs(value: Any, where: str, problems: list[Problem]) -> None:
    """env/headers map NAME -> {env: VAR}. Plain strings are credential literals."""
    if not isinstance(value, dict):
        problems.append(Problem(where, "type", "expected a mapping of names to {env: VAR}"))
        return
    for key, item in value.items():
        item_where = f"{where}.{key}"
        if not isinstance(key, str) or not key:
            problems.append(Problem(item_where, "type", "expected a name"))
        if isinstance(item, dict) and set(item) == {"env"} and isinstance(item["env"], str):
            if not ENV_NAME_RE.match(item["env"]):
                problems.append(Problem(item_where, "env-reference", "invalid variable name"))
        else:
            problems.append(
                Problem(
                    item_where,
                    "credential-literal",
                    "values must be named environment references ({env: VAR}), never literals",
                )
            )


def _normalize_harnesses(
    value: Any, where: str, problems: list[Problem]
) -> dict[str, dict[str, str]]:
    if value is None:
        return {name: {} for name in HARNESSES}
    result: dict[str, dict[str, str]] = {}
    if isinstance(value, list):
        for item in value:
            if item not in HARNESSES:
                problems.append(Problem(where, "harness", f"unknown harness {item!r}"))
            else:
                result[item] = {}
        return result
    if not isinstance(value, dict):
        problems.append(Problem(where, "type", "expected a list or mapping of harnesses"))
        return result
    for name, spec in value.items():
        if name not in HARNESSES:
            problems.append(Problem(f"{where}.{name}", "harness", f"unknown harness {name!r}"))
            continue
        spec = spec or {}
        if not isinstance(spec, dict):
            problems.append(Problem(f"{where}.{name}", "type", "expected a mapping"))
            continue
        _unknown(spec, HARNESS_KEYS, f"{where}.{name}", problems)
        for key, item in spec.items():
            if not isinstance(item, str):
                problems.append(Problem(f"{where}.{name}.{key}", "type", "expected a string"))
            elif key in {"manifest", "path"}:
                check_relpath(item, f"{where}.{name}.{key}", problems)
        result[name] = {k: v for k, v in spec.items() if isinstance(v, str)}
    return result


def _check_source(value: Any, where: str, problems: list[Problem]) -> None:
    if not isinstance(value, dict):
        problems.append(Problem(where, "type", "expected a mapping"))
        return
    _unknown(value, SOURCE_KEYS, where, problems)
    if "git" in value and not SHA_RE.match(str(value.get("sha", ""))):
        problems.append(Problem(f"{where}.sha", "pin", "git sources need a full 40-hex commit sha"))
    if "path" in value:
        check_relpath(value["path"], f"{where}.path", problems)


def _check_heartbeat(value: Any, where: str, problems: list[Problem]) -> None:
    """A heartbeat is a log in the temp directory: ISO time, tool, cwd per line."""
    if not isinstance(value, dict) or set(value) != {"tmpfile"}:
        problems.append(Problem(where, "type", "expected {tmpfile: NAME}"))
    elif not isinstance(value["tmpfile"], str) or not re.match(
        r"^[A-Za-z0-9][A-Za-z0-9._-]*$", value["tmpfile"]
    ):
        problems.append(Problem(where, "path-traversal", "tmpfile must be a plain file name"))


def _check_kind(kind: str, data: dict[str, Any], where: str, problems: list[Problem]) -> None:
    if "heartbeat" in data:
        _check_heartbeat(data["heartbeat"], f"{where}.heartbeat", problems)
    if kind == "mcp":
        transport = data.get("transport", "stdio")
        if transport not in {"stdio", "http"}:
            problems.append(Problem(f"{where}.transport", "value", "expected stdio or http"))
        if transport == "stdio" and not isinstance(data.get("command"), str):
            problems.append(Problem(f"{where}.command", "required", "stdio servers need a command"))
        if transport == "http" and not isinstance(data.get("url"), str):
            problems.append(Problem(f"{where}.url", "required", "http servers need a url"))
        if "args" in data:
            _str_list(data["args"], f"{where}.args", problems)
        for key in ("env", "headers"):
            if key in data:
                _env_refs(data[key], f"{where}.{key}", problems)
        pin = data.get("pin")
        if pin is not None:
            if not isinstance(pin, dict) or set(pin) - {"package", "version"}:
                problems.append(Problem(f"{where}.pin", "type", "expected {package, version}"))
            elif not isinstance(pin.get("version"), str) or pin["version"] in {"", "latest"}:
                problems.append(Problem(f"{where}.pin", "pin", "pin an exact version, not latest"))
            elif "package" in pin and not isinstance(pin["package"], str):
                problems.append(Problem(f"{where}.pin.package", "type", "expected a string"))
        elif transport == "stdio" and data.get("command") in {"npx", "uvx", "bunx", "pnpx"}:
            problems.append(
                Problem(f"{where}.pin", "pin", "package-launched servers must be pinned")
            )
    elif kind == "hook":
        for key in ("event", "matcher"):
            if not isinstance(data.get(key), str):
                problems.append(Problem(f"{where}.{key}", "required", "expected a string"))
        if not _str_list(data.get("command"), f"{where}.command", problems):
            problems.append(Problem(f"{where}.command", "required", "expected a command list"))
        if "timeout" in data and not (
            isinstance(data["timeout"], int) and not isinstance(data["timeout"], bool)
        ):
            problems.append(Problem(f"{where}.timeout", "type", "expected seconds as an integer"))
        if "blocking" in data and not isinstance(data["blocking"], bool):
            problems.append(Problem(f"{where}.blocking", "type", "expected a boolean"))
        if data.get("trust", "requires-user-review") != "requires-user-review":
            problems.append(
                Problem(
                    f"{where}.trust", "trust", "Kit never writes trust; use requires-user-review"
                )
            )
        if "unless_capability" in data and not isinstance(data["unless_capability"], str):
            problems.append(Problem(f"{where}.unless_capability", "type", "expected a string"))
    elif kind == "plugin":
        if "marketplace" in data:
            parse_ref(data["marketplace"], f"{where}.marketplace", problems, "marketplace")
        if "skills" in data:
            _str_list(data["skills"], f"{where}.skills", problems)
        if "hook_provides" in data:
            for slot in _str_list(data["hook_provides"], f"{where}.hook_provides", problems):
                if not SLOT_RE.match(slot):
                    problems.append(Problem(f"{where}.hook_provides", "slot", f"bad slot {slot!r}"))
    elif kind == "skill":
        if "plugin" in data:
            parse_ref(data["plugin"], f"{where}.plugin", problems, "plugin")
    elif kind == "tool":
        check = data.get("check")
        if not isinstance(check, dict) or set(check) - {"command", "expect"}:
            problems.append(Problem(f"{where}.check", "required", "expected {command, expect}"))
        elif not _str_list(check.get("command"), f"{where}.check.command", problems):
            problems.append(Problem(f"{where}.check.command", "required", "expected a command"))
        elif "expect" in check:
            try:
                re.compile(str(check["expect"]))
            except re.error:
                problems.append(Problem(f"{where}.check.expect", "value", "invalid regex"))
        if "install_hint" in data and not isinstance(data["install_hint"], str):
            problems.append(Problem(f"{where}.install_hint", "type", "expected a string"))


def frontmatter(text: str) -> dict[str, Any]:
    """The YAML frontmatter of a Markdown file; {"_error": ...} when it does not parse."""
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not match:
        return {}
    try:
        data = YAML(typ="safe", pure=True).load(match.group(1))
    except Exception:  # malformed frontmatter is a fact, not a crash
        return {"_error": "unparseable frontmatter"}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _check_agent(
    name: str,
    harnesses: dict[str, dict[str, str]],
    assets: Path,
    where: str,
    problems: list[Problem],
) -> None:
    """An agent's asset directory has a ready file for each harness it lists."""
    if not assets.is_dir() or assets.is_symlink():
        problems.append(
            Problem(where, "agent", f"no agent directory {assets.name}/ next to the primitive")
        )
        return
    tail = name.rsplit("/", 1)[-1]
    for harness, filename in AGENT_FILES.items():
        if harness not in harnesses:
            continue
        path = assets / filename
        if path.is_symlink() or not path.is_file():
            problems.append(
                Problem(
                    where, "agent", f"{harness} is listed but {assets.name}/{filename} is missing"
                )
            )
            continue
        text = _read_text(path)
        if text is None:
            problems.append(Problem(where, "agent", f"{assets.name}/{filename} is not UTF-8 text"))
            continue
        if harness == "codex":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                problems.append(
                    Problem(where, "agent", f"{assets.name}/{filename} is not valid TOML: {error}")
                )
                continue
            for key in CODEX_AGENT_KEYS:
                if not isinstance(data.get(key), str) or not data[key].strip():
                    problems.append(
                        Problem(
                            where,
                            "agent",
                            f"{assets.name}/{filename} needs a string {key} (Codex ignores the "
                            "agent without it)",
                        )
                    )
        else:
            front = frontmatter(text)
            if "_error" in front or not front:
                problems.append(
                    Problem(where, "agent", f"{assets.name}/{filename} has no YAML frontmatter")
                )
                continue
            if front.get("name") != tail:
                problems.append(
                    Problem(
                        where,
                        "agent",
                        f"{assets.name}/{filename}: frontmatter name must be {tail!r}, the file "
                        "name Kit writes",
                    )
                )
            if not isinstance(front.get("description"), str) or not front["description"].strip():
                problems.append(
                    Problem(where, "agent", f"{assets.name}/{filename} needs a description")
                )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _normalized_bytes(path: Path) -> bytes:
    """Text assets hash with LF line endings so checkouts agree across platforms."""
    data = path.read_bytes()
    if b"\0" in data:
        return data
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def content_hash(metadata: dict[str, Any], assets: Path | None) -> str:
    """kit-hash-v1: normalized metadata (without hash) plus each asset's path and bytes."""
    digest = hashlib.sha256()
    digest.update(HASH_SCHEME.encode() + b"\0")
    digest.update(_canonical({k: v for k, v in metadata.items() if k != "hash"}))
    if assets is not None and assets.is_dir() and not assets.is_symlink():
        for current, dirs, files in os.walk(assets, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if d != ".git" and not (Path(current) / d).is_symlink()
            )
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                digest.update(b"\0" + path.relative_to(assets).as_posix().encode() + b"\0")
                digest.update(hashlib.sha256(_normalized_bytes(path)).digest())
    return f"{HASH_SCHEME}:sha256:{digest.hexdigest()}"


def _json_safe(value: Any, where: str, problems: list[Problem]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                problems.append(Problem(where, "type", f"keys must be strings, got {key!r}"))
            _json_safe(item, f"{where}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_safe(item, f"{where}[{index}]", problems)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        problems.append(Problem(where, "type", f"unsupported value type {type(value).__name__}"))


def _schema_version(data: dict[str, Any], where: str, problems: list[Problem]) -> int:
    version = data.get("schema_version")
    if version not in SCHEMA_VERSIONS or isinstance(version, bool):
        problems.append(
            Problem(f"{where}.schema_version", "schema-version", f"unsupported {version!r}")
        )
        return 0
    return int(version)


def parse_primitive(root: Path, ref: str) -> Primitive:
    """Load primitives/<dir>/<name>.yaml; assets live in a sibling <name>/ directory."""
    path = primitive_path(root, ref)
    where = path.relative_to(root).as_posix()
    if not contained(root, path):
        raise ModelError([Problem(where, "path-traversal", "resolves outside the warehouse")])
    if not path.is_file():
        raise ModelError([Problem(where, "missing", f"missing primitive {ref}")])
    data = load_yaml(path)
    problems: list[Problem] = []
    if not isinstance(data, dict):
        raise ModelError([Problem(where, "type", "expected a mapping")])
    _json_safe(data, where, problems)
    kind, name = ref.split(":", 1)
    version = _schema_version(data, where, problems)
    if data.get("kind") != kind:
        problems.append(Problem(f"{where}.kind", "kind", f"expected kind {kind!r}"))
    if data.get("name") != name:
        problems.append(Problem(f"{where}.name", "name", f"name must match the file: {name!r}"))
    allowed = V1_PRIMITIVE if version == 1 else COMMON_PRIMITIVE | KIND_FIELDS.get(kind, set())
    _unknown(data, allowed, where, problems)
    harnesses = _normalize_harnesses(data.get("harnesses"), f"{where}.harnesses", problems)
    if "description" in data and not isinstance(data["description"], str):
        problems.append(Problem(f"{where}.description", "type", "expected a string"))
    if "tags" in data:
        _str_list(data["tags"], f"{where}.tags", problems)
    if "source" in data:
        _check_source(data["source"], f"{where}.source", problems)
    # A marketplace is registered by its git URL, external or not.
    source = data.get("source")
    if kind == "marketplace" and not (isinstance(source, dict) and source.get("git")):
        problems.append(Problem(f"{where}.source.git", "required", "a marketplace needs a git URL"))
    for slot in _str_list(data.get("provides", []), f"{where}.provides", problems):
        if not SLOT_RE.match(slot):
            problems.append(Problem(f"{where}.provides", "slot", f"bad slot name {slot!r}"))
    for item in _str_list(data.get("conflicts", []), f"{where}.conflicts", problems):
        parse_ref(item, f"{where}.conflicts", problems)
    if "external" in data and not isinstance(data["external"], bool):
        problems.append(Problem(f"{where}.external", "type", "expected a boolean"))
    if not data.get("external"):
        _check_kind(kind, data, where, problems)
    _scan_secrets(data, where, problems)
    assets = path.with_suffix("")
    if kind == "skill" and assets.is_dir() and not (assets / "SKILL.md").is_file():
        problems.append(Problem(where, "skill", "skill directory has no SKILL.md"))
    if kind == "agent" and not data.get("external"):
        _check_agent(name, harnesses, assets, where, problems)
    digest = content_hash(data, assets)
    if "hash" in data and data["hash"] != digest:
        problems.append(Problem(f"{where}.hash", "hash", "recorded hash does not match content"))
    if problems:
        raise ModelError(problems)
    return Primitive(kind, name, where, data, digest, harnesses)


def _parse_mcp_map(value: Any, where: str, problems: list[Problem]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        problems.append(Problem(where, "type", "expected a mapping of server ids"))
        return {}
    result: dict[str, dict[str, Any]] = {}
    for server, entry in value.items():
        item_where = f"{where}.{server}"
        if not isinstance(server, str) or not SERVER_RE.match(server):
            problems.append(Problem(item_where, "name", f"invalid server id {server!r}"))
            continue
        if not isinstance(entry, dict):
            problems.append(Problem(item_where, "type", "expected {primitive: mcp:NAME}"))
            continue
        _unknown(entry, MCP_ENTRY_KEYS, item_where, problems)
        ref = parse_ref(entry.get("primitive"), f"{item_where}.primitive", problems, "mcp")
        normalized: dict[str, Any] = {"primitive": ref}
        for key in ("enabled_tools", "disabled_tools"):
            if key in entry:
                normalized[key] = sorted(_str_list(entry[key], f"{item_where}.{key}", problems))
        result[server] = normalized
    return result


def _parse_setup(value: Any, where: str, problems: list[Problem]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        problems.append(Problem(where, "type", "expected a list of steps"))
        return []
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(value):
        step_where = f"{where}[{index}]"
        if not isinstance(step, dict):
            problems.append(Problem(step_where, "type", "expected a mapping"))
            continue
        _unknown(step, SETUP_KEYS, step_where, problems)
        run = _str_list(step.get("run"), f"{step_where}.run", problems)
        if not run:
            problems.append(Problem(f"{step_where}.run", "required", "expected a command list"))
        when = step.get("when")
        if when is not None:
            match = WHEN_RE.match(when) if isinstance(when, str) else None
            if not match:
                problems.append(Problem(f"{step_where}.when", "value", "expected file-exists:PATH"))
            else:
                check_relpath(match.group(1), f"{step_where}.when", problems)
        outputs = _str_list(step.get("outputs", []), f"{step_where}.outputs", problems)
        for output in outputs:
            check_relpath(output, f"{step_where}.outputs", problems)
        harness = step.get("harness", "any")
        if harness not in ("any", *HARNESSES):
            problems.append(Problem(f"{step_where}.harness", "harness", f"unknown {harness!r}"))
        steps.append({"run": run, "when": when, "outputs": outputs, "harness": harness})
    return steps


def _parse_budget(value: Any, where: str, problems: list[Problem]) -> dict[str, int]:
    """budget: {tokens, skills}, each optional and a positive integer."""
    if not isinstance(value, dict):
        problems.append(Problem(where, "type", "expected {tokens: N, skills: N}"))
        return {}
    _unknown(value, set(BUDGET_KEYS), where, problems)
    budget: dict[str, int] = {}
    for key in BUDGET_KEYS:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            problems.append(Problem(f"{where}.{key}", "type", "expected a positive integer"))
        else:
            budget[key] = item
    return budget


def _parse_lists(
    data: dict[str, Any], where: str, problems: list[Problem], fields: set[str]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in sorted(fields & set(LIST_FIELDS)):
        if key in data:
            refs = [
                parse_ref(item, f"{where}.{key}", problems, LIST_FIELDS[key])
                for item in _str_list(data[key], f"{where}.{key}", problems)
            ]
            result[key] = [ref for ref in refs if ref]
    return result


def parse_loadout(root: Path, name: str) -> Loadout:
    if is_traversal(name) or not NAME_RE.match(name):
        raise ModelError(
            [
                Problem(
                    name,
                    "path-traversal" if is_traversal(name) else "name",
                    f"invalid loadout name {name!r}",
                )
            ]
        )
    path = loadout_path(root, name)
    where = path.relative_to(root).as_posix()
    if not contained(root, path):
        raise ModelError([Problem(where, "path-traversal", "resolves outside the warehouse")])
    if not path.is_file():
        raise ModelError([Problem(where, "missing", f"missing loadout {name!r}")])
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ModelError([Problem(where, "type", "expected a mapping")])
    problems: list[Problem] = []
    _json_safe(data, where, problems)
    version = _schema_version(data, where, problems)
    _unknown(data, V1_LOADOUT if version == 1 else V2_LOADOUT, where, problems)
    if data.get("name") != name:
        problems.append(Problem(f"{where}.name", "name", f"name must match the file: {name!r}"))
    loadout = Loadout(name=name, path=where, schema_version=version)
    if isinstance(data.get("description"), str):
        loadout.description = data["description"]
    extends = _str_list(data.get("extends", []), f"{where}.extends", problems)
    for parent in extends:
        if is_traversal(parent):
            problems.append(
                Problem(f"{where}.extends", "path-traversal", f"reference escapes: {parent!r}")
            )
        elif not NAME_RE.match(parent):
            problems.append(Problem(f"{where}.extends", "reference", f"invalid {parent!r}"))
        else:
            loadout.extends.append(parent)
    for key, refs in _parse_lists(data, where, problems, set(data)).items():
        setattr(loadout, key, refs)
    loadout.mcp = _parse_mcp_map(data.get("mcp", {}), f"{where}.mcp", problems)
    slots = data.get("slots", {})
    if not isinstance(slots, dict):
        problems.append(Problem(f"{where}.slots", "type", "expected a mapping"))
    else:
        for slot, ref in slots.items():
            if not isinstance(slot, str) or not SLOT_RE.match(slot):
                problems.append(Problem(f"{where}.slots", "slot", f"bad slot name {slot!r}"))
            elif parsed := parse_ref(ref, f"{where}.slots.{slot}", problems):
                loadout.slots[slot] = parsed
    for item in _str_list(data.get("disable", []), f"{where}.disable", problems):
        if parsed := parse_ref(item, f"{where}.disable", problems):
            loadout.disable.append(parsed)
    loadout.setup = _parse_setup(data.get("setup", []), f"{where}.setup", problems)
    if "budget" in data:
        loadout.budget = _parse_budget(data["budget"], f"{where}.budget", problems)
    if "instructions" in data:
        if isinstance(data["instructions"], str):
            loadout.instructions = data["instructions"]
        else:
            problems.append(Problem(f"{where}.instructions", "type", "expected text"))
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        problems.append(Problem(f"{where}.profiles", "type", "expected a mapping"))
        profiles = {}
    for profile_name, spec in profiles.items():
        pwhere = f"{where}.profiles.{profile_name}"
        if not isinstance(profile_name, str) or not re.match(r"^" + _SEGMENT + "$", profile_name):
            problems.append(Problem(pwhere, "name", f"invalid profile name {profile_name!r}"))
            continue
        if not isinstance(spec, dict):
            problems.append(Problem(pwhere, "type", "expected a mapping"))
            continue
        _unknown(spec, PROFILE_FIELDS, pwhere, problems)
        fields: dict[str, Any] = dict(_parse_lists(spec, pwhere, problems, set(spec)))
        if "mcp" in spec:
            fields["mcp"] = _parse_mcp_map(spec["mcp"] or {}, f"{pwhere}.mcp", problems)
        if "setup" in spec:
            fields["setup"] = _parse_setup(spec["setup"] or [], f"{pwhere}.setup", problems)
        if "budget" in spec:
            fields["budget"] = _parse_budget(spec["budget"], f"{pwhere}.budget", problems)
        for key in ("description", "instructions_append"):
            if key in spec:
                if isinstance(spec[key], str):
                    fields[key] = spec[key]
                else:
                    problems.append(Problem(f"{pwhere}.{key}", "type", "expected text"))
        loadout.profiles[profile_name] = Profile(profile_name, fields)
    _scan_secrets(data, where, problems)
    if problems:
        raise ModelError(problems)
    return loadout


def discover(root: Path) -> tuple[list[str], list[str], list[Problem]]:
    """List primitive refs and loadout names in a warehouse; flag stray files."""
    refs: list[str] = []
    loadouts: list[str] = []
    problems: list[Problem] = []
    for kind, directory in KIND_DIRS.items():
        base = root / "primitives" / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.yaml")):
            relative = path.relative_to(base).with_suffix("").as_posix()
            if path.is_symlink() or not contained(root, path):
                problems.append(
                    Problem(path.relative_to(root).as_posix(), "link", "skipped linked file")
                )
            elif NAME_RE.match(relative) and not any(
                # Files inside a primitive's asset directory are assets, not primitives.
                (base / parent).with_suffix(".yaml").is_file()
                for parent in path.relative_to(base).parents
                if parent.name
            ):
                refs.append(f"{kind}:{relative}")
    base = root / "loadouts"
    if base.is_dir():
        for path in sorted(base.glob("*.yaml")):
            if path.is_symlink():
                problems.append(Problem(f"loadouts/{path.name}", "link", "skipped linked file"))
            else:
                loadouts.append(path.stem)
    return refs, loadouts, problems
