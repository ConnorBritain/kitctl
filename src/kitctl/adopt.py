"""`kitctl adopt`: copy a primitive into the warehouse with provenance. Never moves or
edits the original. MCP servers are stored with credentials replaced by ``${NAME}``
references; a value that still looks secret aborts the adoption.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from kitctl import fs, tomlw
from kitctl import paths as kit_paths
from kitctl.hashing import file_digest, tree_digest
from kitctl.safemeta import canonical_hash, frontmatter
from kitctl.scan import normalize_roots, walk_tree
from kitctl.secretscan import find, find_bytes, is_config_name

KIND_DIRS = {
    "skill": "skills",
    "agent": "agents",
    "command": "commands",
    "rules": "rules",
    "hook": "hooks",
    "mcp": "mcp",
    "plugin": "plugins",
}
DESTINATIONS = ("inbox", "primitives", "attic")
# No trailing dot: Windows strips it, so "foo." and "foo" would be one folder.
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9_-])?$")


class AdoptError(ValueError):
    pass


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")
    return cleaned[:64] or "item"


def infer_kind(path: Path) -> str:
    if path.is_dir():
        if (path / "SKILL.md").is_file():
            return "skill"
        if (path / ".claude-plugin" / "plugin.json").is_file() or (
            path / ".codex-plugin" / "plugin.json"
        ).is_file():
            return "plugin"
    parts = set(path.parts[:-1])
    for folder, kind in (
        ("agents", "agent"),
        ("commands", "command"),
        ("rules", "rules"),
        ("hooks", "hook"),
    ):
        if folder in parts:
            return kind
    raise AdoptError(f"Cannot infer what {path} is; pass --kind")


def _target(warehouse: Path, destination: str, kind: str, name: str) -> Path:
    if destination not in DESTINATIONS:
        raise AdoptError(f"--to must be one of {', '.join(DESTINATIONS)}")
    if not SLUG.match(name):
        raise AdoptError(f"Unsafe name {name!r}; use lowercase letters, digits, '.', '_', '-'")
    return warehouse / destination / KIND_DIRS[kind] / name


def _existing_hash(target: Path) -> str | None:
    meta = target / "primitive.toml"
    if not meta.is_file():
        return None
    try:
        return str(tomllib.loads(meta.read_text("utf-8")).get("content_hash", ""))
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def _metadata(
    kind: str,
    name: str,
    origin: str,
    original: str,
    content: str,
    description: str | None,
    harnesses: list[str],
    hook: dict[str, Any] | None = None,
) -> str:
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "name": name,
        "description": description,
        "harnesses": harnesses,
        "content_hash": content,
        "source": {
            "origin": origin,
            "adopted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "original_hash": original,
        },
    }
    if hook:
        document["hook"] = hook
    return tomlw.dumps(
        {k: v for k, v in document.items() if v is not None},
        header="Adopted by kitctl. The original was not modified.",
    )


def _harnesses_for(path: Path) -> list[str]:
    text = path.as_posix()
    found = []
    if "/.claude" in text or "/.claude-plugin" in text:
        found.append("claude")
    if "/.codex" in text or "/.agents" in text:
        found.append("codex")
    return found or ["claude", "codex"]


def _refuse_secrets(files: list[tuple[str, Path]]) -> None:
    """The warehouse is a git repository: never copy credentials into it."""
    for relative, path in files:
        name = Path(relative).name
        if name == ".env" or name.startswith(".env."):
            raise AdoptError(
                f"{relative} looks like an environment file; remove it from the "
                "source (or adopt a copy without it)"
            )
        found = find_bytes(path.read_bytes(), is_config_name(name))
        if found:
            raise AdoptError(
                f"{relative} contains what looks like a credential "
                f"({', '.join(found)}); remove it before adopting"
            )


def adopt_path(
    source: Path,
    warehouse: Path,
    destination: str = "inbox",
    kind: str | None = None,
    name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    source = source.expanduser()
    if not source.exists():
        raise AdoptError(f"Not found: {source}")
    kind = kind or infer_kind(source)
    if kind not in KIND_DIRS or kind == "mcp":
        raise AdoptError("Use `kitctl adopt mcp:<name>` for MCP servers")
    name = name or slug(source.name if source.is_dir() else source.stem)
    target = _target(warehouse, destination, kind, name)
    paths = kit_paths.resolve()
    if source.is_dir():
        allowed = normalize_roots([source, *paths.harness_dirs, warehouse])
        tree = walk_tree(source, allowed)
        files = [(item.relative, item.path) for item in tree.files]
        content = tree_digest([(rel, file_digest(path)) for rel, path in files])
        description: str | None = None
        if (source / "SKILL.md").is_file():
            skill_md = (source / "SKILL.md").read_text("utf-8", errors="replace")
            description = frontmatter(skill_md).get("description")
    else:
        files = [(source.name, source)]
        content = file_digest(source)
        description = frontmatter(source.read_text("utf-8", errors="replace")).get("description")
    _refuse_secrets(files)
    existing = _existing_hash(target)
    if existing == content:
        return {"status": "already adopted", "target": str(target), "content_hash": content}
    if existing is not None and not force:
        raise AdoptError(
            f"{target} already holds a different {kind} named {name!r}; "
            "pass --name for a new name or --force to replace it"
        )
    if existing is not None:
        for old in sorted(target.rglob("*"), reverse=True):
            if old.is_file():
                fs.remove(old)
    for relative, path in files:
        fs.copy_file(path, target / "files" / relative)
    fs.write_atomic(
        target / "primitive.toml",
        _metadata(kind, name, str(source), content, content, description, _harnesses_for(source)),
    )
    return {
        "status": "adopted",
        "target": str(target),
        "content_hash": content,
        "files": len(files),
    }


# -- MCP servers -------------------------------------------------------------------------


def _env_ref(name: str) -> str:
    return "${" + re.sub(r"[^A-Za-z0-9_]", "_", name).upper() + "}"


_SENSITIVE_FLAG = re.compile(
    r"(token|key|secret|password|passwd|auth|credential|bearer)", re.IGNORECASE
)


def _sanitize_args(args: list[Any]) -> list[Any]:
    """Values after sensitive-looking flags (``--token X``, ``--api-key=X``) become refs."""
    clean: list[Any] = []
    previous = ""
    for arg in args:
        text = str(arg)
        flag, eq, _ = text.partition("=")
        if eq and flag.startswith("-") and _SENSITIVE_FLAG.search(flag):
            clean.append(f"{flag}={_env_ref(flag.lstrip('-'))}")
        elif (
            previous.startswith("-")
            and _SENSITIVE_FLAG.search(previous)
            and not text.startswith("-")
        ):
            clean.append(_env_ref(previous.lstrip("-")))
        else:
            clean.append(arg)
        previous = text
    return clean


def sanitize_server(config: Mapping[str, Any]) -> dict[str, Any]:
    """Replace credential-bearing values with ``${NAME}`` references."""
    clean: dict[str, Any] = {}
    for key, value in config.items():
        if key == "args" and isinstance(value, list):
            clean[key] = _sanitize_args(value)
        elif key in {"env", "headers", "http_headers"} and isinstance(value, Mapping):
            clean[key] = {str(k): _env_ref(str(k)) for k in value}
        elif key == "url" and isinstance(value, str):
            parts = urlsplit(value)
            host = parts.hostname or ""
            netloc = host + (f":{parts.port}" if parts.port else "")
            clean[key] = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
        else:
            clean[key] = value
    leaks = find(json.dumps(clean))
    if leaks:
        raise AdoptError(
            f"Server config still contains a likely secret ({', '.join(leaks)}) outside env or "
            "headers; move it to an environment variable before adopting"
        )
    return clean


def _server_config(entry: Mapping[str, Any]) -> Any:
    path = Path(entry["path"])
    name = entry["name"]
    if path.suffix == ".toml":
        document: Any = tomllib.loads(path.read_text("utf-8"))
        return document.get("mcp_servers", {}).get(name)
    document = json.loads(path.read_text("utf-8"))
    if entry["scope"] == "local" and entry["repo"]:
        for project, state in document.get("projects", {}).items():
            if Path(project) == Path(entry["repo"]):
                return state.get("mcpServers", {}).get(name)
    servers = document.get("mcpServers", document) if isinstance(document, Mapping) else {}
    return servers.get(name) if isinstance(servers, Mapping) else None


def adopt_mcp(
    name: str,
    entries: list[dict[str, Any]],
    warehouse: Path,
    destination: str = "inbox",
    harness: str | None = None,
    scope: str | None = None,
    repo: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    matches = [
        e
        for e in entries
        if e["kind"] == "mcp_server"
        and e["name"] == name
        and (harness is None or e["harness"] == harness)
        and (scope is None or e["scope"] == scope)
        and (repo is None or (e["repo"] and Path(e["repo"]) == Path(repo).expanduser()))
    ]
    if not matches:
        raise AdoptError(f"No MCP server named {name!r} in the inventory")
    distinct = {e["content_hash"] for e in matches}
    if len(distinct) > 1:
        options = "; ".join(
            f"{e['harness']}/{e['scope']} {e['repo'] or ''}".strip() for e in matches
        )
        raise AdoptError(
            f"{name!r} has differing definitions ({options}); pass --harness/--scope/--repo"
        )
    entry = matches[0]
    config = _server_config(entry)
    if not isinstance(config, Mapping):
        raise AdoptError(f"Could not read {name!r} from {entry['path']}")
    clean = sanitize_server(config)
    target = _target(warehouse, destination, "mcp", slug(name))
    content = canonical_hash(clean)
    existing = _existing_hash(target)
    if existing == content:
        return {"status": "already adopted", "target": str(target), "content_hash": content}
    if existing is not None and not force:
        raise AdoptError(f"{target} already holds a different server; pass --force to replace it")
    fs.write_atomic(target / "server.json", json.dumps({name: clean}, indent=2) + "\n")
    fs.write_atomic(
        target / "primitive.toml",
        _metadata(
            "mcp",
            slug(name),
            f"{entry['path']}#{entry['harness']}/{entry['scope']}",
            entry["content_hash"] or "",
            content,
            None,
            [entry["harness"]],
        ),
    )
    references = sorted(set(re.findall(r"\$\{[A-Z0-9_]+\}", json.dumps(clean))))
    return {
        "status": "adopted",
        "target": str(target),
        "content_hash": content,
        "env_references": references,
    }


# -- hooks -------------------------------------------------------------------------------

_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_INTERPRETERS = {"sh", "bash", "zsh", "python", "python3", "node", "deno", "bun", "pwsh", "ruby"}


def _raw_hooks(document: Any) -> list[dict[str, Any]]:
    """(event, matcher, handler) rows of a settings-style or hooks.json document."""
    hooks = document.get("hooks") if isinstance(document, Mapping) else None
    if isinstance(hooks, Mapping) and isinstance(hooks.get("hooks"), Mapping):
        hooks = hooks["hooks"]
    rows = []
    for event, groups in (hooks or {}).items() if isinstance(hooks, Mapping) else []:
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, Mapping):
                continue
            for handler in group.get("hooks", []) if isinstance(group.get("hooks"), list) else []:
                if isinstance(handler, Mapping):
                    rows.append(
                        {"event": str(event), "matcher": group.get("matcher"), "handler": handler}
                    )
    return rows


def _expand(token: str, repo: str | None) -> Path:
    home = str(kit_paths.resolve().home)
    # A function replacement: a Windows home (C:\Users\...) is not a valid template.
    text = re.sub(r"^~(?=[/\\]|$)", lambda _: home, token)
    text = text.replace("${HOME}", home).replace("$HOME", home)
    if repo:
        for name in ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR"):
            text = text.replace(name, repo)
    return Path(text)


def adopt_hook(
    name: str,
    entries: list[dict[str, Any]],
    warehouse: Path,
    destination: str = "inbox",
    harness: str | None = None,
    scope: str | None = None,
    repo: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Adopt a hook entry (event, matcher, command, and its local script if any)."""
    import shlex

    from kitctl.compile import credential_problems
    from kitctl.safemeta import sha256_text

    matches = [
        e
        for e in entries
        if e["kind"] == "hook"
        and e["name"] == name
        and not e["provided_by"]
        and Path(e["path"]).suffix == ".json"
        and (harness is None or e["harness"] == harness)
        and (scope is None or e["scope"] == scope)
        and (repo is None or (e["repo"] and Path(e["repo"]) == Path(repo).expanduser()))
    ]
    if not matches:
        raise AdoptError(f"No hook entry named {name!r} (see `kitctl inventory`)")
    if len({e["metadata"].get("command_sha256") for e in matches}) > 1:
        raise AdoptError(f"{name!r} matches different hooks; pass --harness/--scope/--repo")
    entry = matches[0]
    document = json.loads(Path(entry["path"]).read_text("utf-8"))
    wanted = entry["metadata"].get("command_sha256")
    row = None
    for candidate in _raw_hooks(document):
        command = candidate["handler"].get("command")
        args = candidate["handler"].get("args")
        full = command if isinstance(command, str) else ""
        if isinstance(args, list):
            full = json.dumps([full, *map(str, args)])
        if candidate["event"] == entry["metadata"].get("event") and sha256_text(full) == wanted:
            row = candidate
            break
    if row is None or not isinstance(row["handler"].get("command"), str):
        raise AdoptError(f"Could not find {name!r} in {entry['path']} (changed since inventory?)")
    command = row["handler"]["command"]
    if row["handler"].get("args") or row["handler"].get("type", "command") != "command":
        raise AdoptError(f"{name!r} isn't a plain command hook; adopt it by hand")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as error:
        raise AdoptError(f"Can't parse the hook command: {error}") from error
    problems = credential_problems({"args": tokens})
    for token in tokens:
        assignment = _ASSIGNMENT.match(token)
        if (
            assignment
            and "${" not in assignment.group(2)
            and re.search(r"(token|key|secret|password|auth|credential)", assignment.group(1), re.I)
        ):
            problems.append(f"{assignment.group(1)} is set to a literal value")
    if find(command):
        problems.append("the command contains what looks like a credential")
    if problems:
        raise AdoptError(
            f"{name!r}: {'; '.join(problems)}. Move secrets into environment variables first"
        )
    index = 1 if tokens and Path(tokens[0]).name in _INTERPRETERS and len(tokens) > 1 else 0
    script_path = _expand(tokens[index], entry["repo"]) if tokens else None
    files: list[tuple[str, Path]] = []
    spec: dict[str, Any] = {"event": row["event"]}
    if row["matcher"] and row["matcher"] != "*":
        spec["matcher"] = str(row["matcher"])
    if isinstance(row["handler"].get("timeout"), int):
        spec["timeout"] = row["handler"]["timeout"]
    if script_path is not None and script_path.is_file() and not script_path.is_symlink():
        files = [(script_path.name, script_path)]
        spec["script"] = script_path.name
        rest = tokens[:index] + ["{script}"] + tokens[index + 1 :]
        spec["command"] = " ".join(t if t == "{script}" else shlex.quote(t) for t in rest)
        if spec["command"] == "{script}":
            del spec["command"]
    else:
        spec["command"] = command
    _refuse_secrets(files)
    primitive_name = slug(Path(tokens[index]).stem if tokens else name)
    target = _target(warehouse, destination, "hook", primitive_name)
    content = canonical_hash({"hook": spec, "files": {r: file_digest(p) for r, p in files}})
    existing = _existing_hash(target)
    if existing == content:
        return {"status": "already adopted", "target": str(target), "content_hash": content}
    if existing is not None and not force:
        raise AdoptError(f"{target} already holds a different hook; pass --force to replace it")
    for relative, path in files:
        fs.copy_file(path, target / "files" / relative)
        if os.access(path, os.X_OK):
            os.chmod(target / "files" / relative, 0o755)
    fs.write_atomic(
        target / "primitive.toml",
        _metadata(
            "hook",
            primitive_name,
            f"{entry['path']}#{name}",
            entry["content_hash"] or "",
            content,
            None,
            [entry["harness"]],
            hook=spec,
        ),
    )
    return {"status": "adopted", "target": str(target), "content_hash": content}
